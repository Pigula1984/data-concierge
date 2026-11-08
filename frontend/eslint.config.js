import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  // The contract types are generated from the backend OpenAPI schema (npm run api:types).
  { ignores: ["dist/", "src/api/schema.d.ts"] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  reactHooks.configs.flat.recommended,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // Texts from the backend and values from the database are shown as plain text only.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Render backend texts and data as plain text; never inject HTML.",
        },
      ],
      // ECharts is registered piece by piece in src/chart/EChartsHost.ts, which keeps the
      // bundle to the charts and components the page draws.
      "no-restricted-imports": [
        "error",
        {
          paths: [{ name: "echarts", message: "Import from echarts/core and its modules." }],
        },
      ],
    },
  },
  {
    files: ["**/*.js"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
);
