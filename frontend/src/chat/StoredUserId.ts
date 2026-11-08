const STORAGE_KEY = "data-concierge.user-id";
const DEFAULT_USER_ID = "1";
const WHOLE_NUMBER = /^\d+$/;

/**
 * The user id typed on the page, remembered in the browser as a convenience. Storage can be
 * blocked (private mode, site settings), so a failed read or write only loses the memory.
 */
export class StoredUserId {
  private readonly storage: () => Storage;

  constructor(storage: () => Storage = () => window.localStorage) {
    this.storage = storage;
  }

  load(): string {
    try {
      return this.storage().getItem(STORAGE_KEY) ?? DEFAULT_USER_ID;
    } catch {
      return DEFAULT_USER_ID;
    }
  }

  save(text: string): void {
    try {
      this.storage().setItem(STORAGE_KEY, text);
    } catch {
      // Without storage the page still works; the id is just not remembered.
    }
  }

  /** The id the backend accepts, or null when the text is not a whole number. */
  static parse(text: string): number | null {
    const trimmed = text.trim();
    return WHOLE_NUMBER.test(trimmed) ? Number(trimmed) : null;
  }
}
