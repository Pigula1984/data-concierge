import type { CellValue } from "../api/ApiClient";
import type { ColumnType } from "./ColumnType";

// The Polish digit group separator; a no-break space keeps the number on one line.
const GROUP_SEPARATOR = "\u00a0";
const MIN_GROUPED_DIGITS = 5;

/**
 * Formats and orders cell values by their column type, with the rules of `data-concierge chat`:
 * Polish decimal comma, digit groups from five integer digits for decimals only (the response
 * carries no column roles, so ints may be ids), the scale as sent, dates in ISO.
 */
export class ValueFormatter {
  private readonly collator = new Intl.Collator("pl");

  format(value: CellValue, type: ColumnType): string {
    if (value === null) {
      return "";
    }
    if (typeof value === "boolean") {
      return value ? "tak" : "nie";
    }
    if (type.kind !== "decimal") {
      return String(value);
    }
    // Decimals arrive as text with the column's scale, so formatting keeps every digit.
    return this.decimal(String(value));
  }

  /** Missing values first; numbers by value; text in Polish alphabetical order. */
  compare(left: CellValue, right: CellValue, type: ColumnType): number {
    if (left === null || right === null) {
      return Number(left !== null) - Number(right !== null);
    }
    if (type.isNumber) {
      return Number(left) - Number(right);
    }
    return this.collator.compare(String(left), String(right));
  }

  private decimal(text: string): string {
    const negative = text.startsWith("-");
    const digits = negative ? text.slice(1) : text;
    const [whole = "", fraction = ""] = digits.split(".");
    const grouped = whole.length >= MIN_GROUPED_DIGITS ? this.groups(whole) : whole;
    return (negative ? "-" : "") + grouped + (fraction === "" ? "" : `,${fraction}`);
  }

  private groups(whole: string): string {
    const head = whole.length % 3 || 3;
    const parts = [whole.slice(0, head)];
    for (let start = head; start < whole.length; start += 3) {
      parts.push(whole.slice(start, start + 3));
    }
    return parts.join(GROUP_SEPARATOR);
  }
}
