const configuredRepository =
  document.documentElement.dataset.repository?.trim() ?? "";
const pageOwner = location.hostname.endsWith(".github.io")
  ? location.hostname.split(".")[0]
  : "";
const pageProject = location.pathname.split("/").filter(Boolean)[0] ?? "";
const repositoryPattern = /^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/;
const repository = repositoryPattern.test(configuredRepository)
  ? configuredRepository
  : pageOwner && pageProject
    ? `${pageOwner}/${pageProject}`
    : "";
const repositoryURL = repository
  ? `https://github.com/${repository}`
  : "https://github.com";
const latestReleaseURL = `${repositoryURL}/releases/latest`;

function initializeIcons() {
  window.lucide?.createIcons({
    attrs: { "aria-hidden": "true", "stroke-width": "1.7" },
  });
}

function initializeRepositoryLinks() {
  document.querySelectorAll("[data-repository-path]").forEach((link) => {
    link.href = `${repositoryURL}${link.dataset.repositoryPath ?? ""}`;
  });
  document.querySelectorAll("[data-repository-text]").forEach((span) => {
    span.textContent = repository || "OWNER/machboost";
  });
}

function initializeMobileNavigation() {
  const button = document.querySelector(".mobile-menu");
  const navigation = document.getElementById("mobile-nav");
  const close = () => {
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Open navigation");
    navigation.hidden = true;
  };
  button.addEventListener("click", () => {
    const isOpen = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!isOpen));
    button.setAttribute(
      "aria-label",
      isOpen ? "Open navigation" : "Close navigation",
    );
    navigation.hidden = isOpen;
  });
  navigation
    .querySelectorAll("a")
    .forEach((link) => link.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !navigation.hidden) {
      close();
      button.focus();
    }
  });
  matchMedia("(min-width: 801px)").addEventListener("change", close);
}

function initializeTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function select(tab, focus = false) {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden =
        !selected;
    });
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const targets = {
        ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index + tabs.length - 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      if (Object.hasOwn(targets, event.key)) {
        event.preventDefault();
        select(tabs[targets[event.key]], true);
      }
    });
  });
}

function initializeCopyButtons() {
  document.querySelectorAll(".copy-button").forEach((button) => {
    const label = button.getAttribute("aria-label");
    let resetTimer;
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      try {
        await navigator.clipboard.writeText(target.textContent);
        button.setAttribute("aria-label", "Copied");
        button.innerHTML = '<i data-lucide="check"></i>';
        document.getElementById("copy-status").textContent =
          "Example copied to clipboard.";
        initializeIcons();
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
          button.setAttribute("aria-label", label);
          button.innerHTML = '<i data-lucide="copy"></i>';
          initializeIcons();
        }, 1800);
      } catch {
        document.getElementById("copy-status").textContent =
          "Clipboard unavailable. Select the example text to copy it.";
      }
    });
  });
}

async function initializeRelease() {
  const status = document.getElementById("release-status");
  const downloadButtons = document.querySelectorAll("[data-download]");
  if (!repository) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(
      `https://api.github.com/repos/${repository}/releases/latest`,
      {
        headers: { Accept: "application/vnd.github+json" },
        signal: controller.signal,
      },
    );
    if (!response.ok) throw new Error("Release unavailable");
    const release = await response.json();
    const dmg = release.assets?.find((asset) =>
      /^MachBoost-.*-arm64\.dmg$/.test(asset.name),
    );
    const trustedAsset = (asset) => {
      try {
        const url = new URL(asset?.browser_download_url);
        return (
          url.origin === "https://github.com" &&
          url.pathname
            .toLowerCase()
            .startsWith(`/${repository.toLowerCase()}/releases/download/`)
        );
      } catch {
        return false;
      }
    };
    if (!trustedAsset(dmg)) throw new Error("DMG unavailable");
    downloadButtons.forEach((button) => {
      button.href = dmg.browser_download_url;
    });
    const size =
      Number.isFinite(dmg.size) && dmg.size > 0
        ? `${(dmg.size / 1024 / 1024).toFixed(0)} MB`
        : "DMG";
    status.textContent = `${release.tag_name} · ${size} · Apple Silicon · macOS 14+`;
    const checksum = release.assets.find(
      (asset) => asset.name === `${dmg.name}.sha256`,
    );
    if (trustedAsset(checksum)) {
      document.querySelector(".checksum-link").insertAdjacentElement(
        "afterend",
        Object.assign(document.createElement("a"), {
          href: checksum.browser_download_url,
          textContent: "SHA-256",
          className: "checksum-download",
        }),
      );
    }
  } catch {
    downloadButtons.forEach((button) => {
      button.href = latestReleaseURL;
    });
    status.textContent =
      "Apple Silicon · macOS 14+ · Download from GitHub Releases";
  } finally {
    clearTimeout(timeout);
  }
}

initializeIcons();
initializeRepositoryLinks();
initializeMobileNavigation();
initializeTabs();
initializeCopyButtons();
initializeRelease();
