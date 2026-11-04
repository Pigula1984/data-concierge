import type { components } from "../api/schema";

export type ColumnKind = components["schemas"]["ColumnKind"];

const KINDS: readonly ColumnKind[] = ["int", "string", "date", "datetime", "bool", "decimal"];
const TYPE_PATTERN = /^([a-z0-9_]+)(?:\((\d+),(\d+)\))?$/;

/** A column type of the response contract, read from its text form such as `decimal(19,4)`. */
export class ColumnType {
  readonly kind: ColumnKind;
  readonly scale: number;

  private constructor(kind: ColumnKind, scale: number) {
    this.kind = kind;
    this.scale = scale;
  }

  /** An unknown type is shown as text, so a new backend type never breaks the table. */
  static parse(text: string): ColumnType {
    const match = TYPE_PATTERN.exec(text.trim());
    const kind = KINDS.find((known) => known === match?.[1]) ?? "string";
    return new ColumnType(kind, Number(match?.[3] ?? 0));
  }

  get isNumber(): boolean {
    return this.kind === "int" || this.kind === "decimal";
  }
}
