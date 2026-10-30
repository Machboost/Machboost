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
    : "Machboost/Machboost";
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
  const tabs = [...document.querySelectorAll('.code-tabs [role="tab"]')];
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

function autoplayWhileVisible(element, play) {
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const timers = new Set();
  let visible = false;
  let running = false;
  let settle = () => {};
  const later = (action, delay) => {
    if (!running) return;
    const timer = setTimeout(() => {
      timers.delete(timer);
      if (running) action();
    }, delay);
    timers.add(timer);
  };
  const pause = () => {
    running = false;
    timers.forEach(clearTimeout);
    timers.clear();
    settle();
    element.dataset.playback = motion.matches ? "reduced-motion" : "paused";
  };
  const update = () => {
    const allowed =
      visible &&
      !document.hidden &&
      !motion.matches &&
      !element.contains(document.activeElement);
    if (!allowed) {
      if (running) pause();
      else
        element.dataset.playback = motion.matches ? "reduced-motion" : "paused";
    } else if (!running) {
      running = true;
      element.dataset.playback = "playing";
      settle = play(later);
    }
  };
  const observer = new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      update();
    },
    { threshold: 0.2 },
  );
  observer.observe(element);
  element.addEventListener("focusin", update);
  element.addEventListener("focusout", () => queueMicrotask(update));
  document.addEventListener("visibilitychange", update);
  motion.addEventListener("change", update);
  window.addEventListener("pagehide", pause);
  window.addEventListener("pageshow", update);
  update();
}

function initializeWorkflow() {
  const examples = {
    chat: {
      question: "What should I know before the next release?",
      answer:
        "The release notes mention three changes: a new search endpoint, stricter input validation, and an updated client SDK.",
      activity: "Context: release-notes.md",
      input: "Ask a follow-up...",
      label: "YOUR MODEL. YOUR CONTEXT.",
      title: "Pick up the conversation.",
      description:
        "Keep files attached across follow-ups and your model resident between requests. Your next question doesn't have to start over.",
      link: "Meet the app",
      href: "#product",
    },
    code: {
      question: "Add a check for an empty search query.",
      answer:
        "I read the search handler and added input validation. The change is ready for you to review in the workspace diff.",
      activity: "Read search.py → edited validation → review diff",
      input: "Review the change or ask for another...",
      label: "A WORKSPACE, WITH YOUR PERMISSION",
      title: "From context to a change.",
      description:
        "Use a model with tool support to inspect your repository and make focused edits. Choose access permissions and keep the changes in view.",
      link: "Explore coding in the app",
      href: "#product",
    },
    team: {
      question: "Use the team's Studio for this conversation.",
      answer:
        "This conversation uses the shared host's model. Your repository tools stay on this computer; inference requests go to the Studio.",
      activity: "Shared host: Studio · model resident",
      input: "Ask using the connected host...",
      label: "YOUR NETWORK. A SHARED MODEL.",
      title: "Give compute some company.",
      description:
        "Keep models loaded on a shared Mac. Connect with a scoped key and let teammates use the same server from their app or compatible API client.",
      link: "See the shared setup",
      href: "#team",
    },
  };
  const tabs = [...document.querySelectorAll("[data-workflow]")];
  let currentIndex = 0;
  function select(tab, focus = false, later = null) {
    currentIndex = tabs.indexOf(tab);
    const example = examples[tab.dataset.workflow];
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    document
      .getElementById("workflow-panel")
      .setAttribute("aria-labelledby", tab.id);
    for (const field of [
      "question",
      "answer",
      "label",
      "title",
      "description",
    ]) {
      document.getElementById(`demo-${field}`).textContent = example[field];
    }
    document.querySelector("#demo-activity > span").textContent =
      example.activity;
    document.getElementById("demo-input-text").textContent = example.input;
    const link = document.getElementById("demo-link");
    link.replaceChildren(document.createTextNode(`${example.link} `));
    const icon = document.createElement("i");
    icon.dataset.lucide = "arrow-up-right";
    link.append(icon);
    link.href = example.href;
    initializeIcons();
    if (focus) tab.focus();
    const output = document.getElementById("demo-answer");
    const panel = document.getElementById("workflow-panel");
    const finish = () => {
      output.textContent = example.answer;
      panel.setAttribute("aria-busy", "false");
    };
    if (later) {
      output.textContent = "";
      panel.setAttribute("aria-busy", "true");
      let position = 0;
      const write = () => {
        position += 3;
        output.textContent = example.answer.slice(0, position);
        if (position < example.answer.length) later(write, 25);
        else finish();
      };
      later(write, 350);
    } else finish();
    return finish;
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
  autoplayWhileVisible(document.querySelector(".workflow-demo"), (later) => {
    let settle = () => {};
    const cycle = () => {
      settle = select(tabs[currentIndex], false, later);
      later(() => {
        currentIndex = (currentIndex + 1) % tabs.length;
        cycle();
      }, 8000);
    };
    cycle();
    return () => settle();
  });
}

function initializeInferenceExample() {
  const flow = document.getElementById("compute-flow");
  const output = document.getElementById("compute-answer");
  const requestState = document.getElementById("request-state");
  const modelState = document.getElementById("model-state");
  const status = document.getElementById("inference-status");
  const steps = [...flow.querySelectorAll("[data-compute-step]")];
  const answer = output.textContent.replace(/\s+/g, " ").trim();
  const activate = (step) => {
    steps.forEach((item) => {
      item.classList.toggle("is-active", item.dataset.computeStep === step);
    });
  };
  const finish = () => {
    activate(null);
    output.textContent = answer;
    modelState.textContent = "Model kept in memory for follow-ups";
    status.textContent =
      "Example complete. Illustrated playback, not live inference.";
    flow.setAttribute("aria-busy", "false");
  };
  autoplayWhileVisible(document.getElementById("inference-demo"), (later) => {
    const cycle = () => {
      flow.setAttribute("aria-busy", "true");
      requestState.textContent = "Request sent to the Studio";
      modelState.textContent = "Model weights already loaded";
      status.textContent =
        "Illustrative playback: a resident model answers a request.";
      output.textContent = "";
      activate("request");
      later(() => {
        activate("model");
        modelState.textContent = "Processing the prompt with MLX";
      }, 650);
      later(() => {
        activate("answer");
        modelState.textContent = "Streaming the generated answer";
        let position = 0;
        const write = () => {
          position += 2;
          output.textContent = answer.slice(0, position);
          if (position < answer.length) later(write, 30);
          else {
            finish();
            later(cycle, 3500);
          }
        };
        write();
      }, 1300);
    };
    cycle();
    return finish;
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
initializeWorkflow();
initializeInferenceExample();
initializeCopyButtons();
initializeRelease();
