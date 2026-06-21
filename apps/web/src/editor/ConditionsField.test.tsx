import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConditionsField } from "./ConditionsField";

describe("ConditionsField", () => {
  it("renders Add condition button when value is null", () => {
    render(<ConditionsField value={null} onChange={() => {}} />);
    expect(screen.getByText("Add condition")).toBeTruthy();
  });

  it("renders Add condition button when value is empty conditions array", () => {
    render(
      <ConditionsField
        value={{ logic: "AND", conditions: [] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Add condition")).toBeTruthy();
  });

  it("calls onChange with a new condition when Add condition is clicked", () => {
    const onChange = vi.fn();
    render(<ConditionsField value={null} onChange={onChange} />);
    fireEvent.click(screen.getByText("Add condition"));
    expect(onChange).toHaveBeenCalledOnce();
    const arg = onChange.mock.calls[0][0];
    expect(arg).toMatchObject({
      logic: "AND",
      conditions: [expect.objectContaining({ type: "string", operator: "equals" })],
    });
  });

  it("renders existing conditions", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "status", type: "string", operator: "equals", value: "open" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("status")).toBeTruthy();
    expect(screen.getByDisplayValue("open")).toBeTruthy();
  });

  it("removes a condition when the remove button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "status", type: "string", operator: "equals", value: "open" },
          ],
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByLabelText("Remove condition"));
    expect(onChange).toHaveBeenCalledWith(null); // null because conditions is now empty
  });

  it("shows logic toggle (AND/OR) only when there are 2+ conditions", () => {
    const { rerender } = render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    // With 1 condition, no logic toggle
    expect(screen.queryByText("AND")).toBeNull();

    rerender(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
            { id: "c2", field: "b", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    // With 2 conditions, the AND toggle appears
    expect(screen.getByText("AND")).toBeTruthy();
  });

  it("toggles logic from AND to OR when toggle button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "a", type: "string", operator: "equals", value: "x" },
            { id: "c2", field: "b", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("AND"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ logic: "OR" }),
    );
  });

  it("resets operator to first valid when type changes", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "tags", type: "string", operator: "contains", value: "vip" },
          ],
        }}
        onChange={onChange}
      />,
    );
    // Change type to "boolean" — "contains" is not a valid boolean operator
    const typeSelect = screen.getByTitle("Data type");
    fireEvent.change(typeSelect, { target: { value: "boolean" } });
    const updated = onChange.mock.calls[0][0];
    const cond = updated.conditions[0];
    expect(cond.type).toBe("boolean");
    expect(["is true", "is false"]).toContain(cond.operator);
  });

  it("hides value input for no-value operators like 'is empty'", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "note", type: "string", operator: "is empty", value: "" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.queryByPlaceholderText("value")).toBeNull();
  });

  it("shows value input for operators that need a value", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "name", type: "string", operator: "equals", value: "Alice" },
          ],
        }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("Alice")).toBeTruthy();
  });

  it("shows legacy migration banner when conditions empty but allParams has field set", () => {
    render(
      <ConditionsField
        value={null}
        onChange={() => {}}
        allParams={{ field: "status", operator: "equals", value: "open" }}
      />,
    );
    expect(screen.getByText(/legacy single-condition/i)).toBeTruthy();
    expect(screen.getByText("Migrate →")).toBeTruthy();
  });

  it("does not show legacy banner when conditions has entries", () => {
    render(
      <ConditionsField
        value={{
          logic: "AND",
          conditions: [
            { id: "c1", field: "x", type: "string", operator: "equals", value: "y" },
          ],
        }}
        onChange={() => {}}
        allParams={{ field: "status", operator: "equals", value: "open" }}
      />,
    );
    expect(screen.queryByText(/legacy/i)).toBeNull();
  });

  it("calls onChange with migrated condition when Migrate button is clicked", () => {
    const onChange = vi.fn();
    render(
      <ConditionsField
        value={null}
        onChange={onChange}
        allParams={{ field: "score", operator: "greater than", value: "10" }}
      />,
    );
    fireEvent.click(screen.getByText("Migrate →"));
    const arg = onChange.mock.calls[0][0];
    expect(arg.conditions[0]).toMatchObject({
      field: "score",
      operator: "greater than",
      value: "10",
    });
  });
});
