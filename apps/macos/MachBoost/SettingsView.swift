import AppKit
import ServiceManagement
import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @ObservedObject var updates: UpdateController
    @State private var launchAtLogin = false
    @State private var automaticUpdates = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                AppPageHeading("Settings", subtitle: "Preferences for this Mac")

                settingsSection("General", symbol: "slider.horizontal.3") {
                    Toggle("Launch MachBoost at login", isOn: $launchAtLogin)
                        .accessibilityIdentifier("launch-at-login")
                        .onChange(of: launchAtLogin, updateLoginItem)
                }

                settingsSection("Updates", symbol: "arrow.down.circle") {
                    HStack(alignment: .top, spacing: 12) {
                        AppIconTile(symbol: updateIcon, color: updateColor)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(updates.deliveryDescription)
                                .font(.body.weight(.medium))
                            Text(updateDetail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        AppBadge("v\(version)")
                    }

                    Toggle("Check for new releases automatically", isOn: $automaticUpdates)
                        .accessibilityIdentifier("automatic-updates")
                        .onChange(of: automaticUpdates) {
                            updates.automaticallyChecksForUpdates = automaticUpdates
                        }
                        .disabled(!updates.supportsAutomaticUpdates)

                    AppFlowLayout(spacing: 10) {
                        Button {
                            updates.checkForUpdates()
                        } label: {
                            if updates.isChecking {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Label(updates.actionTitle, systemImage: "arrow.clockwise")
                            }
                        }
                        .disabled(!updates.isAvailable || updates.isChecking)
                        .accessibilityIdentifier("check-for-updates")

                        if updates.canDownloadUpdate {
                            Button {
                                updates.downloadUpdate()
                            } label: {
                                Label(updates.downloadTitle, systemImage: "arrow.down.circle")
                            }
                            .buttonStyle(.borderedProminent)
                            .accessibilityIdentifier("download-update")
                        }
                    }
                }

                settingsSection("Storage", symbol: "internaldrive") {
                    storageRow(
                        title: "Models and cache",
                        url: FileManager.default.homeDirectoryForCurrentUser
                            .appendingPathComponent(".cache", isDirectory: true)
                    )
                    storageRow(
                        title: "Chats and attachments",
                        url: applicationSupportURL
                    )
                }

                settingsSection("Privacy", symbol: "lock.shield") {
                    LabeledContent("Chat history") {
                        Text("Stored on this Mac")
                            .foregroundStyle(.secondary)
                    }
                    LabeledContent("Telemetry") {
                        Text("Disabled")
                            .foregroundStyle(.secondary)
                    }
                    LabeledContent("LAN API") {
                        Text(appState.configuration.lanEnabled ? "Authenticated" : "Off")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .toggleStyle(.switch)
            .controlSize(.small)
            .font(.system(size: 13))
            .padding(28)
            .frame(maxWidth: 820)
            .frame(maxWidth: .infinity)
        }
        .background(AppStyle.canvas)
        .tint(AppStyle.accent)
        .navigationTitle("Settings")
        .onAppear {
            launchAtLogin = SMAppService.mainApp.status == .enabled
            automaticUpdates = updates.automaticallyChecksForUpdates
        }
    }

    private func settingsSection<Content: View>(
        _ title: String, symbol: String, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(title, systemImage: symbol)
                .font(.system(size: 13, weight: .semibold))
            VStack(alignment: .leading, spacing: 16, content: content)
                .frame(maxWidth: .infinity, alignment: .leading)
            Divider().padding(.top, 6)
        }
    }

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
            ?? "Development"
    }

    private var updateIcon: String {
        if updates.isChecking { return "arrow.triangle.2.circlepath" }
        if updates.updateAvailable { return "arrow.down.circle.fill" }
        if updates.communityCheckFailed { return "exclamationmark.triangle.fill" }
        return "checkmark.circle.fill"
    }

    private var updateColor: Color {
        if updates.updateAvailable { return AppStyle.accent }
        if updates.communityCheckFailed { return .orange }
        return .secondary
    }

    private var updateDetail: String {
        if updates.supportsAutomaticUpdates {
            if updates.canDownloadUpdate {
                return "Community builds check automatically; download and approval remain manual."
            }
            if let date = updates.lastCheckedAt {
                return "Last checked \(date.formatted(date: .abbreviated, time: .shortened))."
            }
            return "Automatic checks are enabled by default. Signed builds install with Sparkle."
        }
        return "Update checks are unavailable in this build."
    }

    private var applicationSupportURL: URL {
        let root = try? FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return (root ?? FileManager.default.homeDirectoryForCurrentUser)
            .appendingPathComponent("MachBoost", isDirectory: true)
    }

    private func storageRow(title: String, url: URL) -> some View {
        HStack(spacing: 12) {
            AppIconTile(symbol: "folder", color: .secondary, size: 32)
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                Text(url.path)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                NSWorkspace.shared.activateFileViewerSelecting([url])
            } label: {
                Image(systemName: "arrow.up.forward")
            }
            .buttonStyle(AppIconButtonStyle())
            .help("Show in Finder")
        }
    }

    private func updateLoginItem() {
        do {
            if launchAtLogin {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            launchAtLogin = SMAppService.mainApp.status == .enabled
            appState.presentedError = error.localizedDescription
        }
    }
}
