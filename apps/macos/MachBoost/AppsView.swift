import AppKit
import Foundation
import SwiftUI

struct AppsView: View {
    @Environment(AppState.self) private var appState
    @State private var selectedSource = "local"
    @State private var claudeStatus = ClaudeDesktopConnectionStatus.current()
    @State private var chatGPTStatus = ChatGPTDesktopConnectionStatus.current()
    @State private var pendingAction: DesktopAppAction?
    @State private var isWorking = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                AppPageHeading(
                    "Apps",
                    subtitle: "Use resident MachBoost models in desktop and terminal agents."
                )
                inferenceSource
                desktopSection
                terminalSection
            }
            .frame(maxWidth: 900, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity)
        }
        .background(AppStyle.canvas)
        .navigationTitle("Apps")
        .confirmationDialog(
            pendingAction?.title ?? "",
            isPresented: Binding(
                get: { pendingAction != nil },
                set: { if !$0 { pendingAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let pendingAction {
                Button(pendingAction.buttonTitle, role: pendingAction.role) {
                    Task { await apply(pendingAction) }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The selected app will restart. Any running task in it will stop.")
        }
        .onAppear(perform: refreshStatus)
    }

    private var inferenceSource: some View {
        HStack(spacing: 14) {
            AppIconTile(
                symbol: selectedSource == "local" ? "desktopcomputer" : "network",
                size: 38
            )
            VStack(alignment: .leading, spacing: 3) {
                Text("Inference host")
                    .font(.headline)
                Text("Apps below use models already available on this host.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 20)
            Picker("Inference host", selection: $selectedSource) {
                Label("This Mac", systemImage: "desktopcomputer")
                    .tag("local")
                ForEach(appState.teamHosts) { host in
                    Label(host.hostName, systemImage: "network")
                        .tag(host.id.uuidString)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(width: 230)
            .accessibilityIdentifier("apps-host-picker")
        }
        .padding(16)
        .background(AppStyle.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(AppStyle.line, lineWidth: 1)
        }
    }

    private var desktopSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeading("Desktop", detail: "Native apps")
            VStack(spacing: 0) {
                desktopRow(
                    app: .chatGPT,
                    title: "ChatGPT (Codex)",
                    subtitle: "Run Codex tasks with MachBoost models",
                    badge: "Verified",
                    badgeColor: AppStyle.accent,
                    connected: chatGPTStatus.connected,
                    detail: chatGPTStatus.destination,
                    accessibilityID: "chatgpt-desktop-toggle"
                )
                Divider().padding(.leading, 72)
                desktopRow(
                    app: .claude,
                    title: "Claude Desktop",
                    subtitle: "Third-party inference gateway compatibility",
                    badge: "Preview",
                    badgeColor: .orange,
                    connected: claudeStatus.connected,
                    detail: claudeStatus.destination,
                    accessibilityID: "claude-desktop-toggle"
                )
            }
            .background(AppStyle.surface, in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(AppStyle.line, lineWidth: 1)
            }
        }
    }

    private var terminalSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionHeading("Terminal", detail: "Launch commands")
            VStack(spacing: 0) {
                terminalRow(
                    app: .chatGPT,
                    title: "Codex CLI",
                    subtitle: "OpenAI's coding agent with local or shared inference",
                    command: "machboost launch codex",
                    badge: "Verified",
                    badgeColor: AppStyle.accent
                )
                Divider().padding(.leading, 72)
                terminalRow(
                    app: .claude,
                    title: "Claude Code",
                    subtitle: "Anthropic-compatible agent connection",
                    command: claudeCodeCommand,
                    badge: "Preview",
                    badgeColor: .orange
                )
            }
            .background(AppStyle.surface, in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(AppStyle.line, lineWidth: 1)
            }

            Label(
                "OpenAI-compatible clients can also connect directly to \(selectedEndpoint.absoluteString)/v1.",
                systemImage: "link"
            )
            .font(.callout)
            .foregroundStyle(.secondary)
            .textSelection(.enabled)
            .padding(.top, 2)
        }
    }

    private func sectionHeading(_ title: String, detail: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.headline)
            Text(detail)
                .font(.caption)
                .foregroundStyle(.tertiary)
            Spacer()
        }
    }

    private func desktopRow(
        app: InstalledAgentApp,
        title: String,
        subtitle: String,
        badge: String,
        badgeColor: Color,
        connected: Bool,
        detail: String?,
        accessibilityID: String
    ) -> some View {
        HStack(spacing: 14) {
            InstalledApplicationIcon(app: app)

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(title)
                        .font(.headline)
                    AppBadge(badge, color: badgeColor)
                }
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                if connected, let detail {
                    Label(detail, systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(AppStyle.accent)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer(minLength: 16)

            if isWorking {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 38)
            } else {
                Toggle(
                    "Connect \(title)",
                    isOn: Binding(
                        get: { connected },
                        set: { enabled in
                            pendingAction = DesktopAppAction(app: app, connect: enabled)
                        }
                    )
                )
                .labelsHidden()
                .toggleStyle(.switch)
                .accessibilityIdentifier(accessibilityID)
            }
        }
        .padding(18)
    }

    private func terminalRow(
        app: InstalledAgentApp,
        title: String,
        subtitle: String,
        command: String,
        badge: String,
        badgeColor: Color
    ) -> some View {
        HStack(spacing: 14) {
            InstalledApplicationIcon(app: app)
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text(title)
                        .font(.headline)
                    AppBadge(badge, color: badgeColor)
                }
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 18)
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(command, forType: .string)
            } label: {
                HStack(spacing: 7) {
                    Text(command)
                        .font(.system(.caption, design: .monospaced))
                        .lineLimit(1)
                    Image(systemName: "doc.on.doc")
                }
            }
            .buttonStyle(.bordered)
            .help("Copy command")
        }
        .padding(18)
    }

    private var selectedEndpoint: URL {
        guard
            selectedSource != "local",
            let id = UUID(uuidString: selectedSource),
            let host = appState.teamHosts.first(where: { $0.id == id })
        else {
            return appState.configuration.endpoint
        }
        return host.endpoint
    }

    private var claudeCodeCommand: String {
        if selectedSource == "local" {
            return "ANTHROPIC_BASE_URL=\(selectedEndpoint.absoluteString) claude"
        }
        return "machboost use \(selectedHostName) && claude"
    }

    private var selectedHostName: String {
        guard
            let id = UUID(uuidString: selectedSource),
            let host = appState.teamHosts.first(where: { $0.id == id })
        else { return "local" }
        return host.hostName
    }

    private func refreshStatus() {
        claudeStatus = ClaudeDesktopConnectionStatus.current()
        chatGPTStatus = ChatGPTDesktopConnectionStatus.current()
        let endpoint = chatGPTStatus.upstream
            ?? chatGPTStatus.endpoint
            ?? claudeStatus.upstream
            ?? claudeStatus.endpoint
        guard let endpoint else { return }
        if endpoint == appState.configuration.endpoint.absoluteString
            || endpoint == appState.configuration.endpoint.appendingPathComponent("v1").absoluteString {
            selectedSource = "local"
        } else if let host = appState.teamHosts.first(where: {
            endpoint.hasPrefix($0.endpoint.absoluteString)
        }) {
            selectedSource = host.id.uuidString
        }
    }

    private func apply(_ action: DesktopAppAction) async {
        pendingAction = nil
        isWorking = true
        defer { isWorking = false }
        do {
            let integration = action.app == .chatGPT ? "chatgpt" : "claude-desktop"
            if action.connect {
                let token = try await selectedToken()
                _ = try await appState.daemon.runCLI(
                    [
                        "launch",
                        integration,
                        "--endpoint",
                        selectedEndpoint.absoluteString,
                        "--yes",
                    ],
                    apiToken: token
                )
            } else {
                _ = try await appState.daemon.runCLI(
                    ["launch", integration, "--restore", "--yes"]
                )
            }
            refreshStatus()
        } catch {
            appState.presentedError = error.localizedDescription
            refreshStatus()
        }
    }

    private func selectedToken() async throws -> String? {
        if selectedSource == "local" {
            let keychainToken = appState.daemon.authenticationRequired
                ? await Task.detached(priority: .userInitiated) {
                    KeychainStore.token()
                }.value
                : nil
            return try AppsGatewayCredentials.localToken(
                authenticationRequired: appState.daemon.authenticationRequired,
                runtimeToken: appState.apiToken,
                keychainToken: keychainToken
            )
        }
        guard
            let id = UUID(uuidString: selectedSource),
            let token = await KeychainStore.teamTokenAsync(profileID: id),
            !token.isEmpty
        else {
            throw AppsViewError.missingTeamToken
        }
        return token
    }
}

enum AppsGatewayCredentials {
    static func localToken(
        authenticationRequired: Bool,
        runtimeToken: String?,
        keychainToken: String?
    ) throws -> String? {
        guard authenticationRequired else { return nil }
        if let runtimeToken, !runtimeToken.isEmpty {
            return runtimeToken
        }
        if let keychainToken, !keychainToken.isEmpty {
            return keychainToken
        }
        throw AppsViewError.missingLocalToken
    }
}

private enum InstalledAgentApp: Equatable {
    case chatGPT
    case claude

    var paths: [String] {
        switch self {
        case .chatGPT:
            ["/Applications/ChatGPT.app", "/Applications/Codex.app"]
        case .claude:
            ["/Applications/Claude.app"]
        }
    }

    var fallbackSymbol: String {
        switch self {
        case .chatGPT: "circle.hexagongrid.fill"
        case .claude: "sun.max.fill"
        }
    }
}

private struct InstalledApplicationIcon: View {
    let app: InstalledAgentApp

    var body: some View {
        Group {
            if let icon = applicationIcon {
                Image(nsImage: icon)
                    .resizable()
                    .scaledToFit()
            } else {
                Image(systemName: app.fallbackSymbol)
                    .font(.system(size: 25, weight: .medium))
                    .foregroundStyle(AppStyle.accent)
                    .padding(9)
            }
        }
        .frame(width: 42, height: 42)
        .accessibilityHidden(true)
    }

    private var applicationIcon: NSImage? {
        guard let path = app.paths.first(where: FileManager.default.fileExists(atPath:)) else {
            return nil
        }
        let image = NSWorkspace.shared.icon(forFile: path)
        image.size = NSSize(width: 84, height: 84)
        return image
    }
}

private struct DesktopAppAction {
    let app: InstalledAgentApp
    let connect: Bool

    var title: String {
        let name = app == .chatGPT ? "ChatGPT" : "Claude Desktop"
        return connect ? "Connect \(name) to MachBoost?" : "Disconnect \(name) from MachBoost?"
    }

    var buttonTitle: String {
        connect ? "Connect and Restart" : "Disconnect and Restart"
    }

    var role: ButtonRole? {
        connect ? nil : .destructive
    }
}

private struct ChatGPTDesktopConnectionStatus {
    let connected: Bool
    let endpoint: String?
    let upstream: String?
    let relayed: Bool

    var destination: String? { upstream ?? endpoint }

    static func current() -> Self {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let configURL = home.appendingPathComponent(".codex/config.toml")
        let config = (try? String(contentsOf: configURL, encoding: .utf8)) ?? ""
        let endpoint = rootString("openai_base_url", in: config)
        let catalog = rootString("model_catalog_json", in: config)
        let expectedCatalog = home.appendingPathComponent(
            ".codex/machboost-chatgpt-models.json"
        ).path
        let connected = endpoint != nil && catalog == expectedCatalog
        let relay = json(
            at: home.appendingPathComponent(
                ".machboost/chatgpt-loopback-relay.json"
            )
        )
        let relayed = connected
            && relay["schema"] as? String == "machboost.claude-loopback-relay.v1"
            && relay["endpoint"] as? String == endpoint?.replacingOccurrences(of: "/v1", with: "")
        return Self(
            connected: connected,
            endpoint: endpoint,
            upstream: relayed ? relay["upstream"] as? String : nil,
            relayed: relayed
        )
    }
}

private struct ClaudeDesktopConnectionStatus {
    let connected: Bool
    let endpoint: String?
    let upstream: String?
    let relayed: Bool

    var destination: String? { upstream ?? endpoint }

    static func current() -> Self {
        let support = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first
        let library = support?
            .appendingPathComponent("Claude-3p", isDirectory: true)
            .appendingPathComponent("configLibrary", isDirectory: true)
        guard let library else {
            return Self(connected: false, endpoint: nil, upstream: nil, relayed: false)
        }
        let meta = json(at: library.appendingPathComponent("_meta.json"))
        let profile = json(
            at: library.appendingPathComponent(
                "00000000-0000-4000-8000-000000000135.json"
            )
        )
        let connected = meta["appliedId"] as? String
            == "00000000-0000-4000-8000-000000000135"
            && profile["inferenceProvider"] as? String == "gateway"
        let endpoint = connected ? profile["inferenceGatewayBaseUrl"] as? String : nil
        let relay = json(
            at: FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".machboost/claude-loopback-relay.json")
        )
        let relayed = connected
            && relay["schema"] as? String == "machboost.claude-loopback-relay.v1"
            && relay["endpoint"] as? String == endpoint
        return Self(
            connected: connected,
            endpoint: endpoint,
            upstream: relayed ? relay["upstream"] as? String : endpoint,
            relayed: relayed
        )
    }
}

private func rootString(_ key: String, in text: String) -> String? {
    let pattern = "(?m)^[ \\t]*\(NSRegularExpression.escapedPattern(for: key))[ \\t]*=[ \\t]*\"([^\"]+)\"[ \\t]*$"
    guard
        let expression = try? NSRegularExpression(pattern: pattern),
        let match = expression.firstMatch(
            in: text,
            range: NSRange(text.startIndex..., in: text)
        ),
        let range = Range(match.range(at: 1), in: text)
    else { return nil }
    return String(text[range])
}

private func json(at url: URL) -> [String: Any] {
    guard
        let data = try? Data(contentsOf: url),
        let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return [:] }
    return value
}

private enum AppsViewError: LocalizedError {
    case missingLocalToken
    case missingTeamToken

    var errorDescription: String? {
        switch self {
        case .missingLocalToken:
            "This Mac requires authentication, but its MachBoost API key is missing. Turn LAN sharing off and on in Server settings to create a new key."
        case .missingTeamToken:
            "The saved API key for this MachBoost host is missing. Reconnect the host first."
        }
    }
}
