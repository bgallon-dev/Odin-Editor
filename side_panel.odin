package editor

import rl "vendor:raylib"
import "core:fmt"
import "core:strings"

// ---------------------------------------------------------------------------
// Side panel config
// ---------------------------------------------------------------------------
SIDE_PANEL_W       :: 300
SIDE_PANEL_BG      :: rl.Color{28,  28,  28,  255}
SIDE_PANEL_BORDER  :: rl.Color{50,  50,  50,  255}
SIDE_PANEL_HEADER  :: rl.Color{35,  35,  35,  255}
SIDE_PANEL_TEXT    :: rl.Color{180, 180, 180, 255}
SIDE_PANEL_TAB_H   :: 28
SIDE_PANEL_BTN_H   :: 26
SIDE_PANEL_BTN_BG  :: rl.Color{55,  55,  55,  255}
SIDE_PANEL_BTN_HOV :: rl.Color{70,  70,  70,  255}
SIDE_PANEL_LINE_H  :: 22

SEVERITY_INFO_CLR    :: rl.Color{97,  175, 239, 255}  // blue
SEVERITY_WARNING_CLR :: rl.Color{229, 192, 123, 255}  // yellow
SEVERITY_ERROR_CLR   :: rl.Color{224, 108, 117, 255}  // red

// ---------------------------------------------------------------------------
// Panel tab selection
// ---------------------------------------------------------------------------
Panel_Tab :: enum u8 {
    Draft,
    Validation,
}

// ---------------------------------------------------------------------------
// Side panel action results
// ---------------------------------------------------------------------------
Side_Panel_Action :: enum u8 {
    None,
    Accept_Draft,
    Dismiss_Draft,
    Jump_To_Issue,
}

Side_Panel_Result :: struct {
    action:      Side_Panel_Action,
    issue_index: int,
}

// ---------------------------------------------------------------------------
// Side panel state
// ---------------------------------------------------------------------------
Side_Panel :: struct {
    visible:          bool,
    active_tab:       Panel_Tab,
    scroll_y:         int,
    // Draft display
    draft_text:       string,
    draft_ready:      bool,
    draft_confidence: f32,
    drafting:         bool,
    // Validation display
    issues:           [dynamic]Validation_Issue,
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
side_panel_init :: proc(sp: ^Side_Panel) {
    sp.visible = false
    sp.active_tab = .Draft
    sp.scroll_y = 0
    sp.draft_text = ""
    sp.draft_ready = false
    sp.draft_confidence = 0
    sp.drafting = false
    sp.issues = make([dynamic]Validation_Issue)
}

side_panel_destroy :: proc(sp: ^Side_Panel) {
    if len(sp.draft_text) > 0 do delete(sp.draft_text)
    for &issue in sp.issues {
        if len(issue.message) > 0 do delete(issue.message)
    }
    delete(sp.issues)
}

// ---------------------------------------------------------------------------
// Draft management
// ---------------------------------------------------------------------------
side_panel_set_draft :: proc(sp: ^Side_Panel, text: string, confidence: f32) {
    if len(sp.draft_text) > 0 do delete(sp.draft_text)
    sp.draft_text = strings.clone(text)
    sp.draft_confidence = confidence
    sp.draft_ready = true
    sp.drafting = false
    sp.scroll_y = 0
}

side_panel_clear_draft :: proc(sp: ^Side_Panel) {
    if len(sp.draft_text) > 0 do delete(sp.draft_text)
    sp.draft_text = ""
    sp.draft_ready = false
    sp.draft_confidence = 0
}

// ---------------------------------------------------------------------------
// Validation management
// ---------------------------------------------------------------------------
side_panel_set_issues :: proc(sp: ^Side_Panel, issues: []Validation_Issue) {
    // Clear old issues
    for &issue in sp.issues {
        if len(issue.message) > 0 do delete(issue.message)
    }
    clear(&sp.issues)
    // Copy new issues
    for &issue in issues {
        new_issue := Validation_Issue{
            line     = issue.line,
            col      = issue.col,
            end_line = issue.end_line,
            end_col  = issue.end_col,
            severity = issue.severity,
            message  = strings.clone(issue.message),
        }
        append(&sp.issues, new_issue)
    }
    sp.scroll_y = 0
}

side_panel_clear_issues :: proc(sp: ^Side_Panel) {
    for &issue in sp.issues {
        if len(issue.message) > 0 do delete(issue.message)
    }
    clear(&sp.issues)
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
side_panel_render :: proc(sp: ^Side_Panel, font: rl.Font, char_width: f32, win_w: int, win_h: int) -> Side_Panel_Result {
    result := Side_Panel_Result{action = .None, issue_index = -1}
    spacing := f32(0)

    panel_x := win_w - SIDE_PANEL_W
    panel_y := TAB_BAR_H + TOP_MARGIN

    // Background
    rl.DrawRectangle(i32(panel_x), 0, i32(SIDE_PANEL_W), i32(win_h), SIDE_PANEL_BG)
    // Left border
    rl.DrawRectangle(i32(panel_x), 0, 1, i32(win_h), SIDE_PANEL_BORDER)

    // Tab bar
    tab_w := SIDE_PANEL_W / 2
    mx := int(rl.GetMouseX())
    my := int(rl.GetMouseY())

    // Draft tab
    draft_bg := SIDE_PANEL_HEADER if sp.active_tab == .Draft else SIDE_PANEL_BG
    rl.DrawRectangle(i32(panel_x + 1), 0, i32(tab_w), i32(SIDE_PANEL_TAB_H), draft_bg)
    draft_tc := TAB_ACTIVE_TEXT if sp.active_tab == .Draft else TAB_TEXT
    rl.DrawTextEx(font, "Draft", {f32(panel_x + 12), 5}, f32(FONT_SIZE - 2), spacing, draft_tc)
    if sp.active_tab == .Draft {
        rl.DrawRectangle(i32(panel_x + 1), i32(SIDE_PANEL_TAB_H - 2), i32(tab_w), 2, CURSOR_COLOR)
    }

    // Validation tab
    val_x := panel_x + tab_w
    val_bg := SIDE_PANEL_HEADER if sp.active_tab == .Validation else SIDE_PANEL_BG
    rl.DrawRectangle(i32(val_x), 0, i32(tab_w), i32(SIDE_PANEL_TAB_H), val_bg)
    val_tc := TAB_ACTIVE_TEXT if sp.active_tab == .Validation else TAB_TEXT

    // Show issue count in validation tab
    val_label_buf: [32]u8
    val_label := fmt.bprintf(val_label_buf[:], "Issues (%d)", len(sp.issues))
    val_cs := strings.clone_to_cstring(val_label, context.temp_allocator)
    rl.DrawTextEx(font, val_cs, {f32(val_x + 12), 5}, f32(FONT_SIZE - 2), spacing, val_tc)
    if sp.active_tab == .Validation {
        rl.DrawRectangle(i32(val_x), i32(SIDE_PANEL_TAB_H - 2), i32(tab_w), 2, CURSOR_COLOR)
    }

    // Tab clicks
    if rl.IsMouseButtonPressed(.LEFT) && mx >= panel_x && my < SIDE_PANEL_TAB_H {
        if mx < val_x {
            sp.active_tab = .Draft
        } else {
            sp.active_tab = .Validation
        }
        sp.scroll_y = 0
    }

    // Content area
    content_y := SIDE_PANEL_TAB_H + 4
    content_h := win_h - content_y

    switch sp.active_tab {
    case .Draft:
        result = side_panel_render_draft(sp, font, char_width, panel_x, content_y, content_h, win_w, mx, my)
    case .Validation:
        result = side_panel_render_validation(sp, font, char_width, panel_x, content_y, content_h, mx, my)
    }

    // Scroll wheel within panel
    if mx >= panel_x {
        wheel := rl.GetMouseWheelMove()
        if wheel != 0 {
            sp.scroll_y -= int(wheel * 3)
            if sp.scroll_y < 0 do sp.scroll_y = 0
        }
    }

    return result
}

// ---------------------------------------------------------------------------
// Draft tab rendering
// ---------------------------------------------------------------------------
side_panel_render_draft :: proc(sp: ^Side_Panel, font: rl.Font, char_width: f32, panel_x: int, content_y: int, content_h: int, win_w: int, mx: int, my: int) -> Side_Panel_Result {
    result := Side_Panel_Result{action = .None, issue_index = -1}
    spacing := f32(0)

    if sp.drafting {
        rl.DrawTextEx(font, "Drafting...", {f32(panel_x + 12), f32(content_y + 20)}, FONT_SIZE, spacing, rl.Color{229, 192, 123, 255})
        rl.DrawTextEx(font, "(this may take 30s)", {f32(panel_x + 12), f32(content_y + 42)}, f32(FONT_SIZE - 2), spacing, rl.Color{100, 100, 100, 255})
        return result
    }

    if !sp.draft_ready {
        msg := "No draft available"
        mc := strings.clone_to_cstring(msg, context.temp_allocator)
        rl.DrawTextEx(font, mc, {f32(panel_x + 12), f32(content_y + 20)}, FONT_SIZE, spacing, rl.Color{100, 100, 100, 255})
        return result
    }

    // Confidence indicator
    conf_buf: [32]u8
    conf_str := fmt.bprintf(conf_buf[:], "Confidence: %.0f%%", sp.draft_confidence * 100)
    cc := strings.clone_to_cstring(conf_str, context.temp_allocator)
    rl.DrawTextEx(font, cc, {f32(panel_x + 12), f32(content_y + 4)}, f32(FONT_SIZE - 2), spacing, rl.Color{120, 120, 120, 255})

    // Draft text with word wrapping
    text_y := content_y + 24 - sp.scroll_y * SIDE_PANEL_LINE_H
    max_chars_per_line := (SIDE_PANEL_W - 24) / int(char_width)
    if max_chars_per_line < 1 do max_chars_per_line = 1

    draft := sp.draft_text
    line_start := 0
    for line_start < len(draft) {
        line_end := min(line_start + max_chars_per_line, len(draft))
        // Try to break at word boundary
        if line_end < len(draft) {
            wb := line_end
            for wb > line_start && draft[wb] != ' ' && draft[wb] != '\n' { wb -= 1 }
            if wb > line_start do line_end = wb + 1
        }
        // Check for newline
        for j := line_start; j < line_end; j += 1 {
            if draft[j] == '\n' { line_end = j + 1; break }
        }

        if text_y >= content_y && text_y < content_y + content_h {
            segment := draft[line_start:min(line_end, len(draft))]
            // Strip trailing newline for display
            display := segment
            if len(display) > 0 && display[len(display)-1] == '\n' {
                display = display[:len(display)-1]
            }
            sc := strings.clone_to_cstring(string(display), context.temp_allocator)
            rl.DrawTextEx(font, sc, {f32(panel_x + 12), f32(text_y)}, FONT_SIZE, spacing, SIDE_PANEL_TEXT)
        }
        text_y += SIDE_PANEL_LINE_H
        line_start = line_end
    }

    // Buttons at bottom
    btn_y := content_y + content_h - SIDE_PANEL_BTN_H * 2 - 12
    btn_w := (SIDE_PANEL_W - 36) / 2

    // Accept button
    accept_x := panel_x + 12
    accept_hover := mx >= accept_x && mx < accept_x + btn_w && my >= btn_y && my < btn_y + SIDE_PANEL_BTN_H
    accept_bg := SIDE_PANEL_BTN_HOV if accept_hover else SIDE_PANEL_BTN_BG
    rl.DrawRectangle(i32(accept_x), i32(btn_y), i32(btn_w), i32(SIDE_PANEL_BTN_H), accept_bg)
    rl.DrawTextEx(font, "Accept", {f32(accept_x + 8), f32(btn_y + 4)}, f32(FONT_SIZE - 2), spacing, rl.Color{152, 195, 121, 255})

    // Dismiss button
    dismiss_x := accept_x + btn_w + 12
    dismiss_hover := mx >= dismiss_x && mx < dismiss_x + btn_w && my >= btn_y && my < btn_y + SIDE_PANEL_BTN_H
    dismiss_bg := SIDE_PANEL_BTN_HOV if dismiss_hover else SIDE_PANEL_BTN_BG
    rl.DrawRectangle(i32(dismiss_x), i32(btn_y), i32(btn_w), i32(SIDE_PANEL_BTN_H), dismiss_bg)
    rl.DrawTextEx(font, "Dismiss", {f32(dismiss_x + 8), f32(btn_y + 4)}, f32(FONT_SIZE - 2), spacing, rl.Color{224, 108, 117, 255})

    if rl.IsMouseButtonPressed(.LEFT) {
        if accept_hover do result.action = .Accept_Draft
        if dismiss_hover do result.action = .Dismiss_Draft
    }

    return result
}

// ---------------------------------------------------------------------------
// Validation tab rendering
// ---------------------------------------------------------------------------
side_panel_render_validation :: proc(sp: ^Side_Panel, font: rl.Font, char_width: f32, panel_x: int, content_y: int, content_h: int, mx: int, my: int) -> Side_Panel_Result {
    result := Side_Panel_Result{action = .None, issue_index = -1}
    spacing := f32(0)

    if len(sp.issues) == 0 {
        msg := "No issues"
        mc := strings.clone_to_cstring(msg, context.temp_allocator)
        rl.DrawTextEx(font, mc, {f32(panel_x + 12), f32(content_y + 20)}, FONT_SIZE, spacing, rl.Color{100, 100, 100, 255})
        return result
    }

    y := content_y - sp.scroll_y * SIDE_PANEL_LINE_H
    for i := 0; i < len(sp.issues); i += 1 {
        issue := &sp.issues[i]
        row_y := y + i * (SIDE_PANEL_LINE_H * 2 + 4)

        if row_y + SIDE_PANEL_LINE_H * 2 < content_y do continue
        if row_y >= content_y + content_h do break

        // Hover highlight
        is_hover := mx >= panel_x && mx < panel_x + SIDE_PANEL_W && my >= row_y && my < row_y + SIDE_PANEL_LINE_H * 2
        if is_hover {
            rl.DrawRectangle(i32(panel_x + 1), i32(row_y), i32(SIDE_PANEL_W - 1), i32(SIDE_PANEL_LINE_H * 2), rl.Color{40, 40, 40, 255})
        }

        // Severity dot
        sev_color: rl.Color
        switch issue.severity {
        case .Info:    sev_color = SEVERITY_INFO_CLR
        case .Warning: sev_color = SEVERITY_WARNING_CLR
        case .Error:   sev_color = SEVERITY_ERROR_CLR
        }
        dot_x := panel_x + 12
        dot_y := row_y + 6
        rl.DrawCircle(i32(dot_x + 4), i32(dot_y + 4), 4, sev_color)

        // Line:col
        loc_buf: [32]u8
        loc_str := fmt.bprintf(loc_buf[:], "Ln %d, Col %d", issue.line + 1, issue.col + 1)
        lc := strings.clone_to_cstring(loc_str, context.temp_allocator)
        rl.DrawTextEx(font, lc, {f32(dot_x + 14), f32(row_y + 2)}, f32(FONT_SIZE - 2), spacing, sev_color)

        // Message (truncated to fit)
        max_msg_chars := (SIDE_PANEL_W - 30) / int(char_width)
        msg := issue.message
        if len(msg) > max_msg_chars && max_msg_chars > 3 {
            msg = msg[:max_msg_chars - 3]
        }
        mc := strings.clone_to_cstring(msg, context.temp_allocator)
        rl.DrawTextEx(font, mc, {f32(panel_x + 12), f32(row_y + SIDE_PANEL_LINE_H)}, f32(FONT_SIZE - 2), spacing, SIDE_PANEL_TEXT)

        // Click to jump
        if rl.IsMouseButtonPressed(.LEFT) && is_hover {
            result.action = .Jump_To_Issue
            result.issue_index = i
        }
    }

    return result
}
