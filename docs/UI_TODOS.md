# UI Enhancement Todos

## High Impact

- [ ] **Structured Error Display** - Red-bordered error panels with formatted tracebacks, error icons (Medium effort)
- [ ] **Keyboard Shortcuts** - Ctrl+L clear, Ctrl+C interrupt, arrow-key menu navigation (Medium effort)
- [ ] **Code Diff Display** - Before/after comparison when editing code via `e` option (Medium effort)
- [ ] **Output Pagination** - Navigate long outputs (currently capped at 8 visible lines) (Low effort)

## Medium Impact

- [ ] **Network Status Indicator** - Show API connection state, retry attempts (Low effort)
- [ ] **Interactive Menus** - Arrow-key selection for confirmations and %help (Medium effort)
- [ ] **Cross-Platform Voice** - pyttsx3 fallback for non-macOS (Medium effort)

## Polish

- [ ] **Theme Customization** - Light mode, custom color schemes (Medium effort)
- [ ] **High Contrast Mode** - Accessibility improvement (Low effort)
- [ ] **Session Autosave** - Save on interrupt, resume support (Medium effort)
- [ ] **Table Display** - Format SQL results, file listings as tables (Medium effort)

## Code Quality

- [ ] **Replace Silent Exceptions** - Add debug logging instead of `except: pass` (Low effort)
- [ ] **Re-enable Terminal Images** - Fix term_image integration in display_output.py (Medium effort)
