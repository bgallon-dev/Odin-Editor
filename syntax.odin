package editor

import rl "vendor:raylib"

// ---------------------------------------------------------------------------
// Language detection
// ---------------------------------------------------------------------------
Language :: enum u8 {
    Plain,
    Odin,
    Python,
}

// ---------------------------------------------------------------------------
// Token types for syntax coloring
// ---------------------------------------------------------------------------
Token_Kind :: enum u8 {
    Normal,
    Keyword,
    Type,
    Builtin,
    String,
    Char,
    Comment,
    Number,
    Operator,
    Directive,     // Odin: #import, #assert; Python: @decorator
    Proc_Name,     // identifiers followed by ::  proc
}

// ---------------------------------------------------------------------------
// A single colored span within a line
// ---------------------------------------------------------------------------
Token :: struct {
    start:  int,        // byte offset within the line
    length: int,        // byte length
    kind:   Token_Kind,
}

// ---------------------------------------------------------------------------
// Odin keywords
// ---------------------------------------------------------------------------
ODIN_KEYWORDS := [?]string{
    "if", "else", "when", "for", "in", "not_in",
    "switch", "case", "break", "continue", "fallthrough",
    "return", "defer", "using", "do",
    "struct", "union", "enum", "bit_set", "bit_field",
    "proc", "macro",
    "import", "package", "foreign",
    "map", "dynamic", "distinct", "where",
    "context", "or_else", "or_return",
    "asm", "matrix",
    "nil", "true", "false",
    "cast", "auto_cast", "transmute",
    "size_of", "align_of", "offset_of", "type_of", "typeid_of",
}

ODIN_TYPES := [?]string{
    "int", "uint", "i8", "i16", "i32", "i64", "i128",
    "u8", "u16", "u32", "u64", "u128",
    "f16", "f32", "f64",
    "bool", "b8", "b16", "b32", "b64",
    "string", "cstring", "rawptr",
    "rune", "byte",
    "any", "typeid",
    "uintptr",
}

ODIN_BUILTINS := [?]string{
    "append", "delete", "len", "cap",
    "make", "new", "free", "copy",
    "assert", "panic",
    "min", "max", "clamp", "abs",
    "print", "println", "printf",
    "inject_at", "ordered_remove", "unordered_remove",
    "pop", "clear",
}

// ---------------------------------------------------------------------------
// Python keywords
// ---------------------------------------------------------------------------
PYTHON_KEYWORDS := [?]string{
    "if", "elif", "else", "for", "while", "break", "continue",
    "return", "def", "class", "import", "from", "as",
    "with", "try", "except", "finally", "raise",
    "yield", "lambda", "pass", "del",
    "global", "nonlocal", "assert",
    "in", "not", "and", "or", "is",
    "True", "False", "None",
    "async", "await",
}

PYTHON_TYPES := [?]string{
    "int", "float", "str", "bool", "list", "dict", "tuple", "set",
    "bytes", "bytearray", "complex", "frozenset",
    "object", "type", "range", "memoryview",
}

PYTHON_BUILTINS := [?]string{
    "print", "len", "range", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "isinstance", "issubclass",
    "hasattr", "getattr", "setattr", "delattr",
    "super", "property", "staticmethod", "classmethod",
    "open", "input", "repr", "abs", "max", "min", "sum",
    "any", "all", "iter", "next", "id", "hash", "callable",
    "vars", "dir", "help", "hex", "oct", "bin", "ord", "chr",
    "round", "pow", "divmod", "format",
}

// ---------------------------------------------------------------------------
// Dispatch tokenizer — selects language-specific tokenizer
// ---------------------------------------------------------------------------
tokenize_line_for_lang :: proc(line: []u8, lang: Language, in_multiline_string: bool = false, allocator := context.temp_allocator) -> (tokens: [dynamic]Token, still_in_multiline: bool) {
    switch lang {
    case .Python:
        return tokenize_line_python(line, in_multiline_string, allocator)
    case .Odin:
        return tokenize_line(line, allocator), false
    case .Plain:
        return tokenize_line_plain(line, allocator), false
    }
    return tokenize_line(line, allocator), false
}

// ---------------------------------------------------------------------------
// Plain text tokenizer — no highlighting
// ---------------------------------------------------------------------------
tokenize_line_plain :: proc(line: []u8, allocator := context.temp_allocator) -> [dynamic]Token {
    tokens := make([dynamic]Token, allocator)
    if len(line) > 0 {
        append(&tokens, Token{start = 0, length = len(line), kind = .Normal})
    }
    return tokens
}

// ---------------------------------------------------------------------------
// Odin tokenizer (original)
// ---------------------------------------------------------------------------
tokenize_line :: proc(line: []u8, allocator := context.temp_allocator) -> [dynamic]Token {
    tokens := make([dynamic]Token, allocator)
    i := 0
    n := len(line)

    for i < n {
        ch := line[i]

        // --- Line comment: // ---
        if ch == '/' && i + 1 < n && line[i + 1] == '/' {
            append(&tokens, Token{start = i, length = n - i, kind = .Comment})
            break  // rest of line is comment
        }

        // --- Block comment start: /* ---
        if ch == '/' && i + 1 < n && line[i + 1] == '*' {
            start := i
            i += 2
            // Scan to end of block or end of line
            for i < n {
                if line[i] == '*' && i + 1 < n && line[i + 1] == '/' {
                    i += 2
                    break
                }
                i += 1
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Comment})
            continue
        }

        // --- String literal: " ---
        if ch == '"' {
            start := i
            i += 1
            for i < n && line[i] != '"' {
                if line[i] == '\\' && i + 1 < n do i += 1  // skip escaped char
                i += 1
            }
            if i < n do i += 1  // consume closing quote
            append(&tokens, Token{start = start, length = i - start, kind = .String})
            continue
        }

        // --- Raw string: ` ---
        if ch == '`' {
            start := i
            i += 1
            for i < n && line[i] != '`' {
                i += 1
            }
            if i < n do i += 1
            append(&tokens, Token{start = start, length = i - start, kind = .String})
            continue
        }

        // --- Character literal: ' ---
        if ch == '\'' {
            start := i
            i += 1
            for i < n && line[i] != '\'' {
                if line[i] == '\\' && i + 1 < n do i += 1
                i += 1
            }
            if i < n do i += 1
            append(&tokens, Token{start = start, length = i - start, kind = .Char})
            continue
        }

        // --- Directive: # ---
        if ch == '#' {
            start := i
            i += 1
            for i < n && is_ident_char(line[i]) {
                i += 1
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Directive})
            continue
        }

        // --- Number ---
        if is_digit(ch) || (ch == '.' && i + 1 < n && is_digit(line[i + 1])) {
            start := i
            // Handle 0x, 0b, 0o prefixes
            if ch == '0' && i + 1 < n && (line[i + 1] == 'x' || line[i + 1] == 'b' || line[i + 1] == 'o') {
                i += 2
            }
            for i < n && (is_hex_digit(line[i]) || line[i] == '_' || line[i] == '.' || line[i] == 'e' || line[i] == 'E') {
                i += 1
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Number})
            continue
        }

        // --- Identifier or keyword ---
        if is_ident_start(ch) {
            start := i
            for i < n && is_ident_char(line[i]) {
                i += 1
            }
            word := string(line[start:i])

            kind := Token_Kind.Normal
            if is_keyword_odin(word) {
                kind = .Keyword
            } else if is_type_odin(word) {
                kind = .Type
            } else if is_builtin_odin(word) {
                kind = .Builtin
            }

            append(&tokens, Token{start = start, length = i - start, kind = kind})
            continue
        }

        // --- Operators ---
        if is_operator(ch) {
            start := i
            // Consume multi-char operators like ::, ->, !=, <=, etc.
            i += 1
            if i < n {
                pair := [2]u8{ch, line[i]}
                if is_double_operator(pair) {
                    i += 1
                }
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Operator})
            continue
        }

        // --- Whitespace and everything else: Normal ---
        start := i
        for i < n && !is_ident_start(line[i]) && !is_digit(line[i]) &&
            !is_operator(line[i]) && line[i] != '"' && line[i] != '\'' &&
            line[i] != '`' && line[i] != '#' && line[i] != '/' {
            i += 1
        }
        if i > start {
            append(&tokens, Token{start = start, length = i - start, kind = .Normal})
        }
        // Safety: if nothing matched, advance by one to avoid infinite loop
        if i == start {
            i += 1
        }
    }

    return tokens
}

// ---------------------------------------------------------------------------
// Python tokenizer
// ---------------------------------------------------------------------------
tokenize_line_python :: proc(line: []u8, in_multiline_string: bool = false, allocator := context.temp_allocator) -> (tokens: [dynamic]Token, still_in_multiline: bool) {
    tokens = make([dynamic]Token, allocator)
    i := 0
    n := len(line)
    still_in_multiline = false

    // If we're continuing a multi-line string from previous line
    if in_multiline_string {
        start := 0
        for i < n {
            if line[i] == '"' && i + 2 < n && line[i+1] == '"' && line[i+2] == '"' {
                i += 3
                append(&tokens, Token{start = start, length = i - start, kind = .String})
                still_in_multiline = false
                break
            }
            if line[i] == '\'' && i + 2 < n && line[i+1] == '\'' && line[i+2] == '\'' {
                i += 3
                append(&tokens, Token{start = start, length = i - start, kind = .String})
                still_in_multiline = false
                break
            }
            i += 1
        }
        if i >= n {
            // Entire line is still in multi-line string
            append(&tokens, Token{start = 0, length = n, kind = .String})
            still_in_multiline = true
            return
        }
    }

    for i < n {
        ch := line[i]

        // --- Line comment: # ---
        if ch == '#' {
            append(&tokens, Token{start = i, length = n - i, kind = .Comment})
            break
        }

        // --- Decorator: @ ---
        if ch == '@' {
            start := i
            i += 1
            for i < n && is_ident_char(line[i]) {
                i += 1
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Directive})
            continue
        }

        // --- Triple-quoted strings: """ or ''' ---
        if (ch == '"' && i + 2 < n && line[i+1] == '"' && line[i+2] == '"') ||
           (ch == '\'' && i + 2 < n && line[i+1] == '\'' && line[i+2] == '\'') {
            quote_ch := ch
            start := i
            i += 3
            found_end := false
            for i < n {
                if line[i] == quote_ch && i + 2 < n && line[i+1] == quote_ch && line[i+2] == quote_ch {
                    i += 3
                    found_end = true
                    break
                }
                i += 1
            }
            if !found_end {
                // Multi-line string continues on next line
                append(&tokens, Token{start = start, length = n - start, kind = .String})
                still_in_multiline = true
                return
            }
            append(&tokens, Token{start = start, length = i - start, kind = .String})
            continue
        }

        // --- String literal: " or ' ---
        if ch == '"' || ch == '\'' {
            quote := ch
            start := i
            i += 1
            for i < n && line[i] != quote {
                if line[i] == '\\' && i + 1 < n do i += 1
                i += 1
            }
            if i < n do i += 1
            append(&tokens, Token{start = start, length = i - start, kind = .String})
            continue
        }

        // --- Number ---
        if is_digit(ch) || (ch == '.' && i + 1 < n && is_digit(line[i + 1])) {
            start := i
            if ch == '0' && i + 1 < n && (line[i + 1] == 'x' || line[i + 1] == 'b' || line[i + 1] == 'o' || line[i + 1] == 'X' || line[i + 1] == 'B' || line[i + 1] == 'O') {
                i += 2
            }
            for i < n && (is_hex_digit(line[i]) || line[i] == '_' || line[i] == '.' || line[i] == 'e' || line[i] == 'E' || line[i] == 'j' || line[i] == 'J') {
                i += 1
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Number})
            continue
        }

        // --- Identifier or keyword ---
        if is_ident_start(ch) {
            start := i
            for i < n && is_ident_char(line[i]) {
                i += 1
            }
            word := string(line[start:i])

            // Check for string prefixes (f"...", r"...", b"...", etc.)
            if (word == "f" || word == "r" || word == "b" || word == "rb" || word == "br" || word == "fr" || word == "rf" || word == "F" || word == "R" || word == "B") && i < n && (line[i] == '"' || line[i] == '\'') {
                // Back up and let the string handler pick it up next iteration,
                // but color the prefix as part of the string
                prefix_start := start
                quote := line[i]

                // Check for triple quotes
                if i + 2 < n && line[i+1] == quote && line[i+2] == quote {
                    i += 3
                    found_end := false
                    for i < n {
                        if line[i] == quote && i + 2 < n && line[i+1] == quote && line[i+2] == quote {
                            i += 3
                            found_end = true
                            break
                        }
                        i += 1
                    }
                    if !found_end {
                        append(&tokens, Token{start = prefix_start, length = n - prefix_start, kind = .String})
                        still_in_multiline = true
                        return
                    }
                } else {
                    i += 1 // skip opening quote
                    for i < n && line[i] != quote {
                        if line[i] == '\\' && i + 1 < n do i += 1
                        i += 1
                    }
                    if i < n do i += 1
                }
                append(&tokens, Token{start = prefix_start, length = i - prefix_start, kind = .String})
                continue
            }

            kind := Token_Kind.Normal
            if is_keyword_python(word) {
                kind = .Keyword
            } else if is_type_python(word) {
                kind = .Type
            } else if is_builtin_python(word) {
                kind = .Builtin
            }

            append(&tokens, Token{start = start, length = i - start, kind = kind})
            continue
        }

        // --- Operators ---
        if is_operator_python(ch) {
            start := i
            i += 1
            if i < n {
                pair := [2]u8{ch, line[i]}
                if is_double_operator_python(pair) {
                    i += 1
                    // Handle ** and //
                    if i < n && ((pair[0] == '*' && pair[1] == '*') || (pair[0] == '/' && pair[1] == '/')) && line[i] == '=' {
                        i += 1
                    }
                }
            }
            append(&tokens, Token{start = start, length = i - start, kind = .Operator})
            continue
        }

        // --- Whitespace and everything else ---
        start := i
        for i < n && !is_ident_start(line[i]) && !is_digit(line[i]) &&
            !is_operator_python(line[i]) && line[i] != '"' && line[i] != '\'' &&
            line[i] != '#' && line[i] != '@' {
            i += 1
        }
        if i > start {
            append(&tokens, Token{start = start, length = i - start, kind = .Normal})
        }
        if i == start {
            i += 1
        }
    }

    return
}

// ---------------------------------------------------------------------------
// Python-specific operator helpers
// ---------------------------------------------------------------------------
is_operator_python :: proc(ch: u8) -> bool {
    ops := "+-*/%=<>!&|^~:;,.()[]{}?"
    for j := 0; j < len(ops); j += 1 {
        if ch == ops[j] do return true
    }
    return false
}

is_double_operator_python :: proc(pair: [2]u8) -> bool {
    doubles := [?][2]u8{
        {'*', '*'}, {'/', '/'}, {':', '='},
        {'<', '='}, {'>', '='}, {'!', '='}, {'=', '='},
        {'&', '&'}, {'|', '|'},
        {'+', '='}, {'-', '='}, {'*', '='}, {'/', '='},
        {'%', '='}, {'&', '='}, {'|', '='}, {'^', '='},
        {'<', '<'}, {'>', '>'}, {'-', '>'},
    }
    for &d in doubles {
        if pair[0] == d[0] && pair[1] == d[1] do return true
    }
    return false
}

// ---------------------------------------------------------------------------
// Character classification helpers
// ---------------------------------------------------------------------------
is_ident_start :: proc(ch: u8) -> bool {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch == '_'
}

is_ident_char :: proc(ch: u8) -> bool {
    return is_ident_start(ch) || is_digit(ch)
}

is_digit :: proc(ch: u8) -> bool {
    return ch >= '0' && ch <= '9'
}

is_hex_digit :: proc(ch: u8) -> bool {
    return is_digit(ch) || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')
}

is_operator :: proc(ch: u8) -> bool {
    ops := "+-*/%=<>!&|^~:;,.()[]{}@?"
    for j := 0; j < len(ops); j += 1 {
        if ch == ops[j] do return true
    }
    return false
}

is_double_operator :: proc(pair: [2]u8) -> bool {
    doubles := [?][2]u8{
        {':', ':'}, {'-', '>'}, {'<', '='}, {'>', '='},
        {'!', '='}, {'=', '='}, {'&', '&'}, {'|', '|'},
        {'+', '='}, {'-', '='}, {'*', '='}, {'/', '='},
        {'<', '<'}, {'>', '>'}, {'.', '.'},
    }
    for &d in doubles {
        if pair[0] == d[0] && pair[1] == d[1] do return true
    }
    return false
}

// ---------------------------------------------------------------------------
// Language-aware keyword/type/builtin lookups
// ---------------------------------------------------------------------------
is_keyword_odin :: proc(word: string) -> bool {
    for &kw in ODIN_KEYWORDS {
        if word == kw do return true
    }
    return false
}

is_type_odin :: proc(word: string) -> bool {
    for &t in ODIN_TYPES {
        if word == t do return true
    }
    // Also treat PascalCase identifiers (starting uppercase) as types
    if len(word) > 0 && word[0] >= 'A' && word[0] <= 'Z' {
        has_lower := false
        for j := 1; j < len(word); j += 1 {
            if word[j] >= 'a' && word[j] <= 'z' {
                has_lower = true
                break
            }
        }
        if has_lower do return true
    }
    return false
}

is_builtin_odin :: proc(word: string) -> bool {
    for &b in ODIN_BUILTINS {
        if word == b do return true
    }
    return false
}

is_keyword_python :: proc(word: string) -> bool {
    for &kw in PYTHON_KEYWORDS {
        if word == kw do return true
    }
    return false
}

is_type_python :: proc(word: string) -> bool {
    for &t in PYTHON_TYPES {
        if word == t do return true
    }
    // PascalCase as type for Python class names
    if len(word) > 0 && word[0] >= 'A' && word[0] <= 'Z' {
        has_lower := false
        for j := 1; j < len(word); j += 1 {
            if word[j] >= 'a' && word[j] <= 'z' {
                has_lower = true
                break
            }
        }
        if has_lower do return true
    }
    return false
}

is_builtin_python :: proc(word: string) -> bool {
    for &b in PYTHON_BUILTINS {
        if word == b do return true
    }
    return false
}

// Legacy compatibility — used by existing Odin tokenizer
is_keyword :: proc(word: string) -> bool { return is_keyword_odin(word) }
is_type :: proc(word: string) -> bool { return is_type_odin(word) }
is_builtin :: proc(word: string) -> bool { return is_builtin_odin(word) }

// ---------------------------------------------------------------------------
// Color mapping
// ---------------------------------------------------------------------------
get_token_color :: proc(kind: Token_Kind) -> rl.Color {
    switch kind {
    case .Normal:    return cfg.text_color
    case .Keyword:   return rl.Color{198, 120, 221, 255}  // purple
    case .Type:      return rl.Color{229, 192, 123, 255}  // gold
    case .Builtin:   return rl.Color{ 97, 175, 239, 255}  // blue
    case .String:    return rl.Color{152, 195, 121, 255}  // green
    case .Char:      return rl.Color{152, 195, 121, 255}  // green
    case .Comment:   return rl.Color{ 92, 110,  92, 255}  // dim green
    case .Number:    return rl.Color{209, 154, 102, 255}  // orange
    case .Operator:  return rl.Color{171, 178, 191, 255}  // light gray
    case .Directive: return rl.Color{224, 108, 117, 255}  // red
    case .Proc_Name: return rl.Color{ 97, 175, 239, 255}  // blue
    }
    return cfg.text_color
}
