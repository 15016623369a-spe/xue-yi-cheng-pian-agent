import Foundation
import Vision
import ImageIO

// Input files are sampled still frames prepared by the local media worker.
var output: [[String: Any]] = []
do {
    for path in CommandLine.arguments.dropFirst() {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
        try handler.perform([request])
        let items: [[String: Any]] = (request.results ?? []).compactMap { item in
            guard let text = item.topCandidates(1).first else { return nil }
            let b = item.boundingBox
            return ["text": text.string, "confidence": text.confidence,
                    "box": [b.minX, 1 - b.maxY, b.width, b.height]]
        }
        output.append(["items": items])
    }
    let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("Local text recognition failed.\n".utf8))
    exit(1)
}
