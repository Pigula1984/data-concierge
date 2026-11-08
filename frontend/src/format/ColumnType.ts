import type { components } from "../api/schema";

export type ColumnKind = components["schemas"]["ColumnKind"];

const KINDS: readonly ColumnKind[] = ["int", "string", "date", "datetime", "bool", "decimal"];

// The scale of `decimal(38,2)`: the digits after the comma.
const DECIMAL_SCALE = /^decimal\(\s*\d+\s*,\s*(\d+)\s*\)$/;

/** A column type of the response contract, read from its text form such as `decimal(19,4)`. */
export class ColumnType {
  readonly kind: ColumnKind;
  /** Digits after the decimal point; 0 for every other kind. */
  readonly scale: number;

  private constructor(kind: ColumnKind, scale: number) {
    this.kind = kind;
    this.scale = scale;
  }

  /** An unknown type is shown as text, so a new backend type never breaks the table. */
  static parse(text: string): ColumnType {
    // The grid keeps decimals at the scale they were sent with; a chart reads the scale to
    // format the numbers it draws.
    const trimmed = text.trim();
    const name = trimmed.split("(")[0];
    const scale = Number(DECIMAL_SCALE.exec(trimmed)?.[1] ?? 0);
    return new ColumnType(KINDS.find((known) => known === name) ?? "string", scale);
  }

  get isNumber(): boolean {
    return this.kind === "int" || this.kind === "decimal";
  }
}
