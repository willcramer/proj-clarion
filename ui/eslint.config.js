import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // Honor the `_`-prefix "intentionally unused" convention, matching
      // tsconfig's noUnusedParameters/noUnusedLocals behavior so the two
      // type-checkers agree.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      // DX-only Fast Refresh hint: this codebase deliberately co-locates a
      // few constants/hooks with their component/provider (the context
      // files, Layout's nav arrays, KPI cards). Keep it as a signal, not a
      // build-blocking error.
      'react-refresh/only-export-components': 'warn',
      // React-Compiler-era rule from the recommended preset. Resetting or
      // syncing state inside an effect is idiomatic here and pre-dates the
      // rule; surface it as a warning rather than an error. The genuinely
      // dangerous siblings (set-state-in-render, purity, rules-of-hooks)
      // stay as errors.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])
