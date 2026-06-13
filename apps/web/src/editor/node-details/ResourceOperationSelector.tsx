import type { NodeManifest } from "../../types";
import { applyResourceOperation } from "./displayRules";

/** Two-level Resource -> Operation selector for consolidated integration nodes. */
export function ResourceOperationSelector({
  manifest,
  params,
  onChange,
}: {
  manifest: NodeManifest;
  params: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const resources = manifest.integration?.resources ?? [];
  if (resources.length === 0) return null;

  const currentResource =
    resources.find((resource) => resource.id === String(params.resource ?? ""))
      ?.id ?? resources[0].id;
  const resourceDef =
    resources.find((resource) => resource.id === currentResource) ??
    resources[0];
  const operations = resourceDef.operations ?? [];
  const currentOperation =
    operations.find((operation) => operation.id === String(params.operation ?? ""))
      ?.id ??
    operations[0]?.id ??
    "";
  const operationDef = operations.find(
    (operation) => operation.id === currentOperation,
  );

  return (
    <div className="ro-selector">
      <div className="ro-grid">
        <label className="ro-field">
          <span className="field-name">Resource</span>
          <select
            className="field-input"
            value={currentResource}
            onChange={(event) => {
              const resource = resources.find(
                (item) => item.id === event.target.value,
              );
              const firstOperation = resource?.operations[0]?.id ?? "";
              onChange(
                applyResourceOperation(
                  manifest,
                  params,
                  event.target.value,
                  firstOperation,
                ),
              );
            }}
          >
            {resources.map((resource) => (
              <option key={resource.id} value={resource.id}>
                {resource.name}
              </option>
            ))}
          </select>
        </label>
        <label className="ro-field">
          <span className="field-name">Operation</span>
          <select
            className="field-input"
            value={currentOperation}
            onChange={(event) =>
              onChange(
                applyResourceOperation(
                  manifest,
                  params,
                  currentResource,
                  event.target.value,
                ),
              )
            }
          >
            {operations.map((operation) => (
              <option key={operation.id} value={operation.id}>
                {operation.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {operationDef?.description && (
        <p className="ro-desc field-desc">{operationDef.description}</p>
      )}
    </div>
  );
}
