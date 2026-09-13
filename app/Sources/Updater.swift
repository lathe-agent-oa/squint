import Sparkle
import SwiftUI

/// Whether the updater is in a state where a check can be started.
///
/// Sparkle publishes this and the menu item follows it, so the item is greyed
/// while a check or an install is already under way rather than starting a
/// second one.
@MainActor
final class UpdaterAvailability: ObservableObject {
    @Published var canCheck = false

    init(updater: SPUUpdater) {
        updater.publisher(for: \.canCheckForUpdates).assign(to: &$canCheck)
    }
}

/// The "Check for Updates…" item.
///
/// A view rather than a plain `Button` in the command group: the disabled
/// state of a menu item does not track a published value unless the item's own
/// view observes it.
struct CheckForUpdatesView: View {
    @ObservedObject private var availability: UpdaterAvailability
    private let updater: SPUUpdater

    init(updater: SPUUpdater) {
        self.updater = updater
        self.availability = UpdaterAvailability(updater: updater)
    }

    var body: some View {
        Button("Check for Updates…") { updater.checkForUpdates() }
            .disabled(!availability.canCheck)
    }
}
