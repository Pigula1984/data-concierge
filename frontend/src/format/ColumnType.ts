import type { components } from "../api/schema";

export type ColumnKind = components["schemas"]["ColumnKind"];

const KINDS: readonly ColumnKind[] = ["int", "string", "date", "datetime", "bool", "decimal"];

/** A column type of the response contract, read from its text form such as `decimal(19,4)`. */
export class ColumnType {
  readonly kind: ColumnKind;

  private constructor(kind: ColumnKind) {
    this.kind = kind;
  }

  /** An unknown type is shown as text, so a new backend type never breaks the table. */
  static parse(text: string): ColumnType {
    // Decimals keep the scale they were sent with, so only the kind before `(` matters.
    const name = text.trim().split("(")[0];
    return new ColumnType(KINDS.find((known) => known === name) ?? "string");
  }

  get isNumber(): boolean {
    return this.kind === "int" || this.kind === "decimal";
  }
}
