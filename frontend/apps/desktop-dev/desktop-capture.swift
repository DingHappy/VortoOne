import AppKit
import Carbon.HIToolbox
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

private let maxPNGBytes = 768 * 1024

private func png(_ image: CGImage) -> Data? {
    for scale in [1, 0.75, 0.5, 0.32] {
        var frame = image
        if scale < 1 {
            let width = max(1, Int(Double(image.width) * scale))
            let height = max(1, Int(Double(image.height) * scale))
            guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
            context.interpolationQuality = .medium
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            guard let reduced = context.makeImage() else { return nil }
            frame = reduced
        }
        let data = NSMutableData()
        guard let encoder = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(encoder, frame, nil)
        if CGImageDestinationFinalize(encoder), data.length <= maxPNGBytes { return data as Data }
    }
    return nil
}

private func writeFrame(_ data: Data) throws {
    var size = UInt32(data.count).bigEndian
    try FileHandle.standardOutput.write(contentsOf: Data(bytes: &size, count: 4))
    try FileHandle.standardOutput.write(contentsOf: data)
}

private func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

private func elementAttribute(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}

private func windowRect(_ window: AXUIElement) -> CGRect? {
    guard let position = attribute(window, kAXPositionAttribute), CFGetTypeID(position) == AXValueGetTypeID(),
          let size = attribute(window, kAXSizeAttribute), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position as! AXValue, .cgPoint, &point),
          AXValueGetValue(size as! AXValue, .cgSize, &dimensions),
          dimensions.width > 0, dimensions.height > 0 else { return nil }
    return CGRect(origin: point, size: dimensions)
}

private func focusedField(on display: CGRect) -> AXUIElement? {
    guard !IsSecureEventInputEnabled(), let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return nil }
    let application = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(application, 0.15)
    guard let window = elementAttribute(application, kAXFocusedWindowAttribute),
          let rect = windowRect(window), display.contains(rect),
          let field = elementAttribute(application, kAXFocusedUIElementAttribute),
          let fieldWindow = elementAttribute(field, kAXWindowAttribute), CFEqual(window, fieldWindow),
          let fieldRect = windowRect(field), display.contains(fieldRect),
          let role = attribute(field, kAXRoleAttribute) as? String,
          role == kAXTextFieldRole || role == kAXTextAreaRole else { return nil }
    var subrole: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(field, kAXSubroleAttribute as CFString, &subrole)
    if result == .success {
        guard let name = subrole as? String, name != kAXSecureTextFieldSubrole else { return nil }
    } else if result != .noValue && result != .attributeUnsupported {
        return nil
    }
    var writable = DarwinBoolean(false)
    guard AXUIElementIsAttributeSettable(field, kAXSelectedTextAttribute as CFString, &writable) == .success,
          writable.boolValue else { return nil }
    return field
}

private func clickLocation(on display: CGRect, column: Int, row: Int) -> CGPoint? {
    guard (0..<10_000).contains(column), (0..<10_000).contains(row) else { return nil }
    let point = CGPoint(x: display.minX + display.width * CGFloat(column) / 10_000,
                        y: display.minY + display.height * CGFloat(row) / 10_000)
    return display.contains(point) ? point : nil
}

private func click(_ point: CGPoint) -> Bool {
    guard !IsSecureEventInputEnabled(), let source = CGEventSource(stateID: .privateState),
          let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) else { return false }
    down.flags = []
    up.flags = []
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    return true
}

private struct ControlDisplay {
    let id: CGDirectDisplayID
    let bounds: CGRect

    @available(macOS 15.2, *)
    init?(filter: SCContentFilter) {
        guard filter.includedDisplays.count == 1, let display = filter.includedDisplays.first else { return nil }
        id = display.displayID
        bounds = CGDisplayBounds(id)
        guard bounds.size == filter.contentRect.size, isCurrent else { return nil }
    }

    var isCurrent: Bool {
        CGDisplayIsActive(id) != 0 && CGDisplayIsInMirrorSet(id) == 0 && bounds == CGDisplayBounds(id)
    }
}

private struct ControlFrame {
    let expires: TimeInterval
    let field: AXUIElement?
}

@available(macOS 14, *)
private final class DisplayShare: NSObject, SCContentSharingPickerObserver {
    // Selection, input and frame state are only accessed on the main queue.
    private let allowControl: Bool
    private var selection: SCContentFilter?
    private var display: ControlDisplay?
    private var frame: ControlFrame?
    private var controlling = false
    private var capturing = false
    private var pending = Data()
    private var expires: TimeInterval = 0

    init(allowControl: Bool) {
        self.allowControl = allowControl
    }

    func present() {
        FileHandle.standardInput.readabilityHandler = { [self] input in
            let command = input.availableData
            DispatchQueue.main.async {
                if command.isEmpty { exit(0) }
                self.receive(command)
            }
        }
        let picker = SCContentSharingPicker.shared
        var configuration = SCContentSharingPickerConfiguration()
        configuration.allowedPickerModes = [.singleDisplay]
        configuration.allowsChangingSelectedContent = false
        picker.defaultConfiguration = configuration
        picker.add(self)
        picker.isActive = true
        picker.present(using: .display)
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        DispatchQueue.main.async { exit(0) }
    }

    func contentSharingPickerStartDidFailWithError(_ error: Error) {
        DispatchQueue.main.async { exit(1) }
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        DispatchQueue.main.async { [self] in
            guard selection == nil, stream == nil, filter.style == .display else { exit(1) }
            if allowControl {
                guard #available(macOS 15.2, *), let target = ControlDisplay(filter: filter) else { exit(1) }
                display = target
            }
            selection = filter
            expires = ProcessInfo.processInfo.systemUptime + 300
            send(allowControl ? 67 : 82)
        }
    }

    private func send(_ response: UInt8) {
        do { try FileHandle.standardOutput.write(contentsOf: Data([response])) }
        catch { exit(1) }
    }

    private func receive(_ bytes: Data) {
        guard !capturing, pending.count + bytes.count <= 512 else { exit(1) }
        pending.append(bytes)
        guard let newline = pending.firstIndex(of: 10) else { return }
        guard newline == pending.index(before: pending.endIndex) else { exit(1) }
        let command = pending.prefix(upTo: newline)
        pending.removeAll()
        if command.elementsEqual("capture".utf8) {
            capture()
            return
        }
        guard let display, display.isCurrent, CGPreflightPostEventAccess(), AXIsProcessTrusted(),
              let observed = frame, ProcessInfo.processInfo.systemUptime < observed.expires else { exit(1) }
        frame = nil
        if !controlling && command.elementsEqual("control".utf8) {
            controlling = true
            send(6)
            return
        }
        let prefix = "input ".utf8
        guard controlling, command.starts(with: prefix),
              let action = try? JSONSerialization.jsonObject(with: Data(command.dropFirst(prefix.count))) as? [String: Any] else { exit(1) }
        send(apply(action, display: display, observed: observed) ? 2 : 7)
    }

    private func apply(_ action: [String: Any], display: ControlDisplay, observed: ControlFrame) -> Bool {
        switch action["kind"] as? String {
        case "click":
            guard Set(action.keys) == ["kind", "x", "y"],
                  let x = action["x"] as? NSNumber, let y = action["y"] as? NSNumber,
                  CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
                  let column = Int(exactly: x.doubleValue), let row = Int(exactly: y.doubleValue),
                  let point = clickLocation(on: display.bounds, column: column, row: row) else { exit(1) }
            return display.isCurrent && click(point)
        case "text":
            guard Set(action.keys) == ["kind", "text"], let text = action["text"] as? String,
                  !text.isEmpty, text.utf8.count <= 96,
                  !text.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) || CharacterSet.newlines.contains($0) }) else { exit(1) }
            guard let previous = observed.field, let field = focusedField(on: display.bounds), CFEqual(previous, field),
                  display.isCurrent, !IsSecureEventInputEnabled(),
                  ProcessInfo.processInfo.systemUptime < observed.expires else { return false }
            return AXUIElementSetAttributeValue(field, kAXSelectedTextAttribute as CFString, text as CFString) == .success
        default:
            exit(1)
        }
    }

    private func capture() {
        guard let selection, !capturing, ProcessInfo.processInfo.systemUptime < expires else { exit(1) }
        if let display {
            guard display.isCurrent, CGPreflightPostEventAccess(), AXIsProcessTrusted() else { exit(1) }
        }
        frame = nil
        let focusBefore = controlling ? display.flatMap { focusedField(on: $0.bounds) } : nil
        let size = selection.contentRect.size
        guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 else { exit(1) }
        let scale = min(1, 1280 / size.width, 900 / size.height)
        let configuration = SCStreamConfiguration()
        configuration.width = max(1, Int(size.width * scale))
        configuration.height = max(1, Int(size.height * scale))
        configuration.showsCursor = false
        configuration.capturesAudio = false
        capturing = true
        SCScreenshotManager.captureImage(contentFilter: selection, configuration: configuration) { [self] image, error in
            guard error == nil, let image, let data = png(image) else { exit(1) }
            DispatchQueue.main.async {
                let now = ProcessInfo.processInfo.systemUptime
                guard now < self.expires else { exit(1) }
                if let display = self.display {
                    guard display.isCurrent else { exit(1) }
                    let focusAfter = focusBefore.flatMap { previous in
                        focusedField(on: display.bounds).flatMap { CFEqual(previous, $0) ? $0 : nil }
                    }
                    self.frame = ControlFrame(expires: min(self.expires, now + 3), field: focusAfter)
                }
                do { try writeFrame(data) }
                catch { exit(1) }
                self.capturing = false
            }
        }
    }
}

@main
private struct DesktopCapture {
    @MainActor
    static func main() throws {
        if CommandLine.arguments.dropFirst().elementsEqual(["--self-test"]) {
            let sampleDisplay = CGRect(x: -1920, y: -48, width: 1920, height: 1080)
            guard clickLocation(on: sampleDisplay, column: 5000, row: 2500) == CGPoint(x: -960, y: 222),
                  clickLocation(on: sampleDisplay, column: 10_000, row: 5000) == nil else { exit(1) }
            guard let sample = CGContext(data: nil, width: 32, height: 24, bitsPerComponent: 8,
                                         bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                         bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { exit(1) }
            sample.setFillColor(red: 0.2, green: 0.4, blue: 0.7, alpha: 1)
            sample.fill(CGRect(x: 0, y: 0, width: 32, height: 24))
            guard let image = sample.makeImage(), let data = png(image) else { exit(1) }
            try writeFrame(data)
            return
        }
        let arguments = Array(CommandLine.arguments.dropFirst())
        let control = arguments == ["--share-control"]
        guard #available(macOS 14, *), control || arguments == ["--share"] else { exit(1) }
        if control {
            guard #available(macOS 15.2, *), CGPreflightPostEventAccess(), AXIsProcessTrusted() else { exit(1) }
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let share = DisplayShare(allowControl: control)
        DispatchQueue.main.async {
            app.activate()
            share.present()
        }
        withExtendedLifetime(share) { app.run() }
    }
}
