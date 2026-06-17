import { describe, expect, it } from "vitest";
import {
  buildNodeExpression,
  flattenOutputFields,
  formatValuePreview,
  getUpstreamNodeIds,
  getUpstreamNodes,
  inferType,
  searchUpstreamFields,
} from "./upstreamFields";

describe("inferType", () => {
  it("returns str for string", () => expect(inferType("hello")).toBe("str"));
  it("returns int for whole number", () => expect(inferType(42)).toBe("int"));
  it("returns float for decimal", () => expect(inferType(3.14)).toBe("float"));
  it("returns bool for boolean", () => expect(inferType(true)).toBe("bool"));
  it("returns list for array", () => expect(inferType([1, 2])).toBe("list"));
  it("returns obj for plain object", () => expect(inferType({ a: 1 })).toBe("obj"));
  it("returns null for null", () => expect(inferType(null)).toBe("null"));
  it("returns null for undefined", () => expect(inferType(undefined)).toBe("null"));
});

describe("formatValuePreview", () => {
  it("truncates long strings to 12 chars", () => {
    expect(formatValuePreview("hello world foo bar")).toBe('"hello world…"');
  });
  it("shows short strings quoted", () => {
    expect(formatValuePreview("Alice")).toBe('"Alice"');
  });
  it("formats arrays as item count", () => {
    expect(formatValuePreview([1, 2, 3])).toBe("[3 items]");
  });
  it("formats objects with first two keys", () => {
    expect(formatValuePreview({ a: 1, b: 2, c: 3 })).toBe("{a, b…}");
  });
  it("formats numbers directly", () => {
    expect(formatValuePreview(42)).toBe("42");
  });
});

describe("buildNodeExpression", () => {
  it("wraps path in double-brace node syntax", () => {
    expect(buildNodeExpression("abc123", "name")).toBe(
      '{{ $node["abc123"].main.name }}',
    );
  });
  it("handles nested paths", () => {
    expect(buildNodeExpression("abc123", "orders[0].id")).toBe(
      '{{ $node["abc123"].main.orders[0].id }}',
    );
  });
});

describe("flattenOutputFields", () => {
  it("flattens a flat object to top-level fields", () => {
    const fields = flattenOutputFields({ name: "Alice", id: 42 }, "node1");
    expect(fields).toHaveLength(2);
    expect(fields[0].path).toBe("name");
    expect(fields[0].type).toBe("str");
    expect(fields[0].expression).toBe('{{ $node["node1"].main.name }}');
    expect(fields[1].path).toBe("id");
    expect(fields[1].type).toBe("int");
  });

  it("marks objects and arrays as expandable", () => {
    const fields = flattenOutputFields({ orders: [{ id: 1 }], meta: { x: 1 } }, "n");
    const orders = fields.find((f) => f.path === "orders")!;
    const meta = fields.find((f) => f.path === "meta")!;
    expect(orders.isExpandable).toBe(true);
    expect(meta.isExpandable).toBe(true);
  });

  it("flattens one level into nested paths", () => {
    const fields = flattenOutputFields({ user: { name: "Bob", age: 30 } }, "n");
    expect(fields.some((f) => f.path === "user.name")).toBe(true);
    expect(fields.some((f) => f.path === "user.age")).toBe(true);
  });

  it("caps array expansion at 5 items", () => {
    const arr = [1, 2, 3, 4, 5, 6, 7];
    const fields = flattenOutputFields({ items: arr }, "n");
    const arrayChildren = fields.filter((f) => f.path.startsWith("items["));
    expect(arrayChildren.length).toBeLessThanOrEqual(5);
  });

  it("does not exceed depth 3", () => {
    const deep = { a: { b: { c: { d: "too deep" } } } };
    const fields = flattenOutputFields(deep, "n");
    expect(fields.every((f) => f.path.split(".").length <= 3)).toBe(true);
  });

  it("returns empty array for non-object/array values", () => {
    expect(flattenOutputFields("string", "n")).toHaveLength(0);
    expect(flattenOutputFields(42, "n")).toHaveLength(0);
    expect(flattenOutputFields(null, "n")).toHaveLength(0);
  });
});

describe("getUpstreamNodeIds", () => {
  const edges = [
    { source: "a", target: "b" },
    { source: "b", target: "c" },
    { source: "x", target: "d" },
  ];

  it("finds direct upstream nodes", () => {
    const ids = getUpstreamNodeIds("b", edges as never);
    expect(ids).toContain("a");
  });

  it("finds transitive upstream nodes via BFS", () => {
    const ids = getUpstreamNodeIds("c", edges as never);
    expect(ids).toContain("b");
    expect(ids).toContain("a");
  });

  it("excludes nodes from disconnected branches", () => {
    const ids = getUpstreamNodeIds("c", edges as never);
    expect(ids).not.toContain("x");
    expect(ids).not.toContain("d");
  });

  it("returns empty array for a node with no upstream", () => {
    expect(getUpstreamNodeIds("a", edges as never)).toHaveLength(0);
  });
});

describe("getUpstreamNodes", () => {
  const nodes = [
    { id: "a", data: { label: "HTTP Request", manifest: { name: "HTTP Request" }, params: {} } },
    { id: "b", data: { manifest: { name: "JSON Parse" }, params: {} } },
  ] as never;
  const edges = [{ source: "a", target: "b" }] as never;

  it("returns upstream node with label from data.label", () => {
    const result = getUpstreamNodes("b", nodes, edges, {
      a: { main: { status: 200 } },
    });
    expect(result).toHaveLength(1);
    expect(result[0].label).toBe("HTTP Request");
    expect(result[0].id).toBe("a");
  });

  it("falls back to manifest.name when label is absent", () => {
    const result = getUpstreamNodes("b", nodes, edges, { a: { main: { x: 1 } } });
    expect(result[0].label).toBe("HTTP Request");
  });

  it("unwraps the main port from runOutputs", () => {
    const result = getUpstreamNodes("b", nodes, edges, {
      a: { main: { name: "Alice" } },
    });
    expect(result[0].fields.some((f) => f.path === "name")).toBe(true);
  });

  it("returns empty fields when node has not run", () => {
    const result = getUpstreamNodes("b", nodes, edges, {});
    expect(result[0].fields).toHaveLength(0);
  });
});

describe("searchUpstreamFields", () => {
  const upstream = [
    {
      id: "a",
      label: "Node A",
      fields: [
        { path: "email", type: "str" as const, valuePreview: '"alice@co.com"', expression: '{{ $node["a"].main.email }}', isExpandable: false },
        { path: "id", type: "int" as const, valuePreview: "42", expression: '{{ $node["a"].main.id }}', isExpandable: false },
      ],
    },
  ];

  it("returns all nodes when query is empty", () => {
    expect(searchUpstreamFields(upstream, "")).toHaveLength(1);
  });

  it("filters by field path", () => {
    const result = searchUpstreamFields(upstream, "email");
    expect(result[0].fields).toHaveLength(1);
    expect(result[0].fields[0].path).toBe("email");
  });

  it("filters by value preview content", () => {
    const result = searchUpstreamFields(upstream, "alice");
    expect(result[0].fields).toHaveLength(1);
  });

  it("removes nodes with no matching fields", () => {
    expect(searchUpstreamFields(upstream, "zzznomatch")).toHaveLength(0);
  });
});
