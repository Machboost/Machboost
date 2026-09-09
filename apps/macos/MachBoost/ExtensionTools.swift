import Foundation
import MachBoostDaemonClient

@MainActor
enum ExtensionTools {
    static let names: Set<String> = ["search_mcp_tools", "call_mcp_tool"]

    static let definitions: [APIToolDefinition] = [
        APIToolDefinition(
            function: .init(
                name: "search_mcp_tools",
                description: "Find tools exposed by enabled MCP connectors. After choosing a result, call call_mcp_tool with its exact server_id, name, and arguments.",
                parameters: .object([
                    "type": .string("object"),
                    "properties": .object([
                        "query": .object(["type": .string("string")]),
                        "limit": .object([
                            "type": .string("integer"),
                            "minimum": .number(1),
                            "maximum": .number(25),
                        ]),
                    ]),
                    "required": .array([.string("query")]),
                ])
            )
        ),
        APIToolDefinition(
            function: .init(
                name: "call_mcp_tool",
                description: "Call one tool returned by search_mcp_tools. Copy server_id and name exactly. Omit arguments when the selected tool takes no input.",
                parameters: .object([
                    "type": .string("object"),
                    "properties": .object([
                        "server_id": .object(["type": .string("string")]),
                        "name": .object(["type": .string("string")]),
                        "arguments": .object(["type": .string("object")]),
                    ]),
                    "required": .array([.string("server_id"), .string("name")]),
                ])
            )
        ),
    ]

    static func supports(_ call: APIToolCall) -> Bool {
        names.contains(call.function.name)
    }

    static func requiresApproval(_ call: APIToolCall) -> Bool {
        call.function.name == "call_mcp_tool"
    }

    static func execute(_ call: APIToolCall, appState: AppState) async throws -> CodingToolResult {
        let arguments = object(call.function.arguments)
        switch call.function.name {
        case "search_mcp_tools":
            let query = try requiredString(arguments, "query")
            let limit = boundedInt(arguments["limit"], default: 8, range: 1 ... 25)
            let tools = try await appState.searchMCPTools(query: query, limit: limit)
            let data = try JSONEncoder().encode(tools)
            return CodingToolResult(
                callID: call.id,
                name: call.function.name,
                content: "Choose a tool and call call_mcp_tool with its exact server_id and name.\n"
                    + String(decoding: data, as: UTF8.self),
                changedPath: nil,
                changePatch: nil
            )
        case "call_mcp_tool":
            let serverID = try requiredString(arguments, keys: ["server_id", "serverId"])
            let name = try requiredString(arguments, keys: ["name", "tool", "tool_name"])
            guard let normalizedArguments = normalizedObject(arguments["arguments"] ?? .object([:])) else {
                throw CodingWorkspaceError.invalidArguments("arguments must be a JSON object")
            }
            let result = try await appState.callMCPTool(
                serverID: serverID,
                name: name,
                arguments: .object(normalizedArguments)
            )
            let content = json([
                "server": result.serverName,
                "tool": result.tool,
                "is_error": result.isError,
                "content": result.text,
            ])
            return CodingToolResult(
                callID: call.id,
                name: call.function.name,
                content: content,
                changedPath: nil,
                changePatch: nil
            )
        default:
            throw CodingWorkspaceError.invalidArguments("Unknown extension tool: \(call.function.name)")
        }
    }

    static func normalizedObject(_ value: JSONValue?) -> [String: JSONValue]? {
        switch value {
        case let .object(object):
            return object
        case let .string(source):
            guard let data = source.data(using: .utf8),
                  let decoded = try? JSONDecoder().decode(JSONValue.self, from: data),
                  case let .object(object) = decoded else {
                return nil
            }
            return object
        case .none:
            return [:]
        default:
            return nil
        }
    }

    private static func object(_ value: JSONValue?) -> [String: JSONValue] {
        normalizedObject(value) ?? [:]
    }

    private static func string(_ value: JSONValue?) -> String? {
        guard case let .string(string) = value else { return nil }
        return string
    }

    private static func requiredString(
        _ object: [String: JSONValue],
        _ key: String
    ) throws -> String {
        try requiredString(object, keys: [key])
    }

    private static func requiredString(
        _ object: [String: JSONValue],
        keys: [String]
    ) throws -> String {
        guard let value = keys.lazy.compactMap({ string(object[$0]) }).first?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else {
            throw CodingWorkspaceError.invalidArguments("\(keys[0]) is required")
        }
        return value
    }

    private static func boundedInt(
        _ value: JSONValue?,
        default defaultValue: Int,
        range: ClosedRange<Int>
    ) -> Int {
        guard case let .number(number) = value else { return defaultValue }
        return min(max(Int(number), range.lowerBound), range.upperBound)
    }

    private static func json(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys, .withoutEscapingSlashes]
        ) else { return #"{"error":"tool result could not be encoded"}"# }
        return String(decoding: data, as: UTF8.self)
    }
}
