interface UserIdFieldProps {
  value: string;
  valid: boolean;
  onChange: (text: string) => void;
}

/** There is no sign-in yet: the user id is typed, as with `--user` in the CLI. */
export function UserIdField({ value, valid, onChange }: UserIdFieldProps) {
  return (
    <div className="user-id">
      <label>
        Użytkownik (user_id){" "}
        <input
          value={value}
          inputMode="numeric"
          size={6}
          aria-invalid={!valid}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
      </label>
      {!valid && (
        <p className="user-id-hint" role="alert">
          Podaj identyfikator użytkownika jako liczbę całkowitą, np. 1.
        </p>
      )}
    </div>
  );
}
