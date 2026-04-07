package editor

import rl "vendor:raylib"
import "core:fmt"
import "core:strings"

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
editor_render :: proc(ed: ^Editor_State) {
    // Reset temp allocator at start of each frame to prevent memory growth
    free_all(context.temp_allocator)

    rl.BeginDrawing()
    rl.ClearBackground(BG_COLOR)

    win_w := int(rl.GetScreenWidth())
    win_h := int(rl.GetScreenHeight())
    buf := tab_active_buf(&ed.tab_bar)
    visible := get_visible_lines(ed)
    left_m := get_left_margin(ed)
    text_top := get_text_top()
    spacing := f32(0)
    gutter_x := left_m - GUTTER_W
    if ed.file_browser.visible do gutter_x = SIDEBAR_W

    // === Tab bar ===
    mx := int(rl.GetMouseX())
    my := int(rl.GetMouseY())
    sidebar_off := 0
    if ed.file_browser.visible do sidebar_off = SIDEBAR_W
    rl.DrawRectangle(i32(sidebar_off), 0, i32(win_w - sidebar_off), i32(TAB_BAR_H), TAB_BG)
    for i := 0; i < len(ed.tab_bar.tabs); i += 1 {
        tx := sidebar_off + i * TAB_W
        is_active := i == ed.tab_bar.active
        bg := TAB_ACTIVE_BG if is_active else TAB_BG
        tc := TAB_ACTIVE_TEXT if is_active else TAB_TEXT
        rl.DrawRectangle(i32(tx), 0, i32(TAB_W), i32(TAB_BAR_H), bg)
        rl.DrawRectangle(i32(tx + TAB_W - 1), 0, 1, i32(TAB_BAR_H), TAB_BORDER)
        if is_active do rl.DrawRectangle(i32(tx), i32(TAB_BAR_H - 2), i32(TAB_W), 2, CURSOR_COLOR)

        tb := &ed.tab_bar.tabs[i]
        name_buf: [64]u8
        dirty_m := "*" if tb.dirty else ""
        name_str := fmt.bprintf(name_buf[:], "%s%s", dirty_m, tb.filepath)
        // Truncate if too long — leave room for close button
        max_chars := (TAB_W - 28) / int(ed.char_width)
        if len(name_str) > max_chars && max_chars > 3 {
            name_str = name_str[:max_chars]
        }
        nc := strings.clone_to_cstring(name_str, context.temp_allocator)
        rl.DrawTextEx(ed.font, nc, {f32(tx + 6), 5}, f32(FONT_SIZE - 2), spacing, tc)

        // Close button
        close_x := i32(tx + TAB_W - 20)
        close_y := i32(4)
        close_w :: 16
        close_h :: 20
        close_hover := mx >= int(close_x) && mx < int(close_x + close_w) &&
                       my >= int(close_y) && my < int(close_y + close_h)
        close_color := rl.Color{200, 80, 80, 255} if close_hover else tc
        rl.DrawTextEx(ed.font, "x", {f32(close_x + 3), f32(close_y + 1)}, f32(FONT_SIZE - 2), spacing, close_color)
    }

    // === Current line highlight ===
    csl := buf.cursor.line - buf.scroll_y
    if csl >= 0 && csl < visible {
        cy := i32(text_top + csl * LINE_HEIGHT)
        rl.DrawRectangle(i32(left_m - GUTTER_W), cy, i32(win_w), i32(LINE_HEIGHT), CURLINE_COLOR)
    }

    // === Text rendering ===
    all_content := piece_table_content(&buf.pt, context.temp_allocator)
    raw := transmute([]u8)all_content

    has_sel := cursor_has_selection(&buf.cursor)
    sel_s := cursor_sel_start(&buf.cursor)
    sel_e := cursor_sel_end(&buf.cursor)

    // Find bracket match
    bracket_match_off := find_matching_bracket(&buf.pt, buf.cursor.offset)

    search_len := 0
    if buf.find.active && buf.find.search_len > 0 do search_len = buf.find.search_len

    // Track multi-line string state for Python
    in_multiline_string := false
    // We need to compute state for lines before scroll_y too
    pre_line := 0; pre_lso := 0
    if buf.language == .Python && buf.scroll_y > 0 {
        for pre_lso <= len(raw) && pre_line < buf.scroll_y {
            pre_leo := pre_lso; for pre_leo < len(raw) && raw[pre_leo] != '\n' { pre_leo += 1 }
            lb := raw[pre_lso:pre_leo]
            _, in_multiline_string = tokenize_line_for_lang(lb, buf.language, in_multiline_string)
            pre_line += 1; pre_lso = pre_leo + 1; if pre_leo >= len(raw) do break
        }
    }

    line_num := 0; lso := 0
    if buf.language == .Python && buf.scroll_y > 0 {
        line_num = pre_line; lso = pre_lso
    }
    for lso <= len(raw) {
        leo := lso; for leo < len(raw) && raw[leo] != '\n' { leo += 1 }
        sl := line_num - buf.scroll_y
        if sl >= visible do break

        lb := raw[lso:leo]

        if sl >= 0 {
            y := f32(text_top + sl * LINE_HEIGHT)

            // Search highlights
            if search_len > 0 {
                for mi := 0; mi < len(buf.find.matches); mi += 1 {
                    mo := buf.find.matches[mi]; me := mo + search_len
                    if me > lso && mo < leo {
                        hs := max(mo, lso) - lso; he := min(me, leo) - lso
                        sx := f32(left_m) + f32(hs) * ed.char_width; w := f32(he - hs) * ed.char_width
                        clr := SEARCH_CUR_MATCH if (buf.find.current_match >= 0 && buf.find.current_match < len(buf.find.matches) && buf.find.matches[buf.find.current_match] == mo) else SEARCH_MATCH_CLR
                        rl.DrawRectangle(i32(sx), i32(y), i32(w), i32(LINE_HEIGHT), clr)
                    }
                }
            }

            // Bracket highlight
            if bracket_match_off >= lso && bracket_match_off < leo {
                bc := bracket_match_off - lso
                bx := f32(left_m) + f32(bc) * ed.char_width
                rl.DrawRectangle(i32(bx), i32(y), i32(ed.char_width), i32(LINE_HEIGHT), BRACKET_HL)
            }
            // Also highlight the bracket at cursor
            if buf.cursor.offset >= lso && buf.cursor.offset < leo && bracket_match_off >= 0 {
                ch_at := char_at_offset(&buf.pt, buf.cursor.offset)
                if ch_at == '(' || ch_at == ')' || ch_at == '{' || ch_at == '}' || ch_at == '[' || ch_at == ']' {
                    bc := buf.cursor.offset - lso
                    bx := f32(left_m) + f32(bc) * ed.char_width
                    rl.DrawRectangle(i32(bx), i32(y), i32(ed.char_width), i32(LINE_HEIGHT), BRACKET_HL)
                }
            }

            // Selection
            if has_sel { draw_line_selection_at(ed, lso, leo, sel_s, sel_e, sl, left_m) }

            // Tokens
            tokens: [dynamic]Token
            tokens, in_multiline_string = tokenize_line_for_lang(lb, buf.language, in_multiline_string)
            for &tok in tokens {
                color := get_token_color(tok.kind); col := tok.start
                tbs := lb[tok.start : tok.start + tok.length]
                for j := 0; j < len(tbs); j += 1 {
                    x := f32(left_m) + f32(col) * ed.char_width
                    cb: [2]u8; cb[0] = tbs[j]; cb[1] = 0
                    rl.DrawTextEx(ed.font, cast(cstring)&cb[0], {x, y + 2}, FONT_SIZE, spacing, color)
                    col += 1
                }
            }
        } else {
            // Off-screen but still need to track multi-line string state for Python
            _, in_multiline_string = tokenize_line_for_lang(lb, buf.language, in_multiline_string)
        }
        line_num += 1; lso = leo + 1; if leo >= len(raw) do break
    }

    // Line numbers
    for i := 0; i < visible; i += 1 {
        ln := buf.scroll_y + i; if ln >= buf.pt.total_lines do break
        y := f32(text_top + i * LINE_HEIGHT)
        nb: [16]u8; ns := fmt.bprintf(nb[:], "%3d", ln + 1)
        nc := strings.clone_to_cstring(ns, context.temp_allocator)
        rl.DrawTextEx(ed.font, nc, {f32(gutter_x + 2), y + 2}, FONT_SIZE, spacing, LINENUM_COLOR)
    }

    // Cursor
    if ed.blink_on && csl >= 0 && csl < visible {
        cx := f32(left_m) + f32(buf.cursor.col) * ed.char_width
        cy := f32(text_top + csl * LINE_HEIGHT)
        rl.DrawRectangle(i32(cx), i32(cy), 2, i32(LINE_HEIGHT), CURSOR_COLOR)
    }

    // === Minimap ===
    if ed.show_minimap {
        mm_x := win_w - MINIMAP_W
        if ed.side_panel.visible do mm_x -= SIDE_PANEL_W
        rl.DrawRectangle(i32(mm_x), i32(text_top), i32(MINIMAP_W), i32(win_h - text_top), MINIMAP_BG)

        // Viewport indicator
        mm_total := buf.pt.total_lines
        if mm_total > 0 {
            mm_h := win_h - text_top - LINE_HEIGHT
            view_start := int(f32(buf.scroll_y) / f32(mm_total) * f32(mm_h))
            view_h := int(f32(visible) / f32(mm_total) * f32(mm_h))
            if view_h < 8 do view_h = 8
            rl.DrawRectangle(i32(mm_x), i32(text_top + view_start), i32(MINIMAP_W), i32(view_h), MINIMAP_VIEW)
        }

        // Render minimap text (simplified — one pixel per char)
        mm_line := 0; mm_lso := 0
        for mm_lso <= len(raw) {
            mm_leo := mm_lso; for mm_leo < len(raw) && raw[mm_leo] != '\n' { mm_leo += 1 }
            mm_y := text_top + mm_line * MINIMAP_LINE_H
            if mm_y >= win_h - LINE_HEIGHT do break

            line_len := mm_leo - mm_lso
            // Draw a tiny colored line for non-empty lines
            if line_len > 0 {
                draw_w := min(line_len, MINIMAP_W / int(MINIMAP_CHAR_W))
                rl.DrawRectangle(i32(mm_x + 2), i32(mm_y), i32(f32(draw_w) * MINIMAP_CHAR_W), i32(MINIMAP_LINE_H - 1), MINIMAP_TEXT)
            }

            mm_line += 1; mm_lso = mm_leo + 1; if mm_leo >= len(raw) do break
        }

        // Minimap click to scroll
        if rl.IsMouseButtonPressed(.LEFT) && int(rl.GetMouseX()) >= mm_x {
            mm_my := int(rl.GetMouseY()) - text_top
            mm_h := win_h - text_top - LINE_HEIGHT
            if mm_h > 0 && buf.pt.total_lines > 0 {
                target := int(f32(mm_my) / f32(mm_h) * f32(buf.pt.total_lines))
                buf.scroll_y = clamp(target - visible/2, 0, get_max_scroll(ed))
            }
        }
    }

    // === Status bar ===
    status_y := win_h - LINE_HEIGHT
    if buf.find.active { status_y -= SEARCH_BAR_H; if buf.find.show_replace do status_y -= SEARCH_BAR_H }
    sbg := STATUS_BG; if ed.warn_timer > 0 do sbg = STATUS_WARN_BG
    rl.DrawRectangle(0, i32(status_y), i32(win_w), i32(LINE_HEIGHT), sbg)

    stb: [512]u8; dm := "*" if buf.dirty else ""; fd := buf.filepath if len(buf.filepath) > 0 else "[new]"
    ss: string
    if ed.warn_timer > 0 {
        ss = fmt.bprintf(stb[:], " UNSAVED! Ctrl+S to save  |  %s%s  |  Ln %d, Col %d", dm, fd, buf.cursor.line+1, buf.cursor.col+1)
    } else {
        mi := ""; mb: [64]u8
        if buf.find.active && len(buf.find.matches) > 0 { cur := (buf.find.current_match + 1) if buf.find.current_match >= 0 else 0; mi = fmt.bprintf(mb[:], "  |  Match %d/%d", cur, len(buf.find.matches)) }
        sym_info := ""; sym_buf: [32]u8
        if memory_is_active(&ed.ipc) {
            sym_info = fmt.bprintf(sym_buf[:], "  |  %d symbols", memory_symbol_count(&ed.ipc))
        }
        ss = fmt.bprintf(stb[:], " %s%s  |  Ln %d, Col %d  |  %d lines%s%s  |  Ctrl+B browser  Ctrl+P open  Ctrl+J panel", dm, fd, buf.cursor.line+1, buf.cursor.col+1, buf.pt.total_lines, mi, sym_info)
    }
    sc := strings.clone_to_cstring(ss, context.temp_allocator)
    rl.DrawTextEx(ed.font, sc, {4, f32(status_y) + 2}, FONT_SIZE, spacing, STATUS_FG)

    // === Search bar ===
    if buf.find.active {
        sy := status_y + LINE_HEIGHT
        rl.DrawRectangle(0, i32(sy), i32(win_w), i32(SEARCH_BAR_H), SEARCH_BG)
        rl.DrawRectangle(0, i32(sy), i32(win_w), 1, SEARCH_BORDER)
        lbl := "Find: "; lc := strings.clone_to_cstring(lbl, context.temp_allocator)
        rl.DrawTextEx(ed.font, lc, {8, f32(sy)+4}, FONT_SIZE, spacing, STATUS_FG)
        fx := f32(8) + 6*ed.char_width
        st := string(buf.find.search_buf[:buf.find.search_len]); stc := strings.clone_to_cstring(st, context.temp_allocator)
        fc := TEXT_COLOR if !buf.find.focus_replace else rl.Color{150,150,150,255}
        rl.DrawTextEx(ed.font, stc, {fx, f32(sy)+4}, FONT_SIZE, spacing, fc)
        if !buf.find.focus_replace && ed.blink_on { scx := fx + f32(buf.find.search_len)*ed.char_width; rl.DrawRectangle(i32(scx), i32(sy+3), 2, i32(FONT_SIZE), CURSOR_COLOR) }

        if buf.find.show_replace {
            ry := sy + SEARCH_BAR_H
            rl.DrawRectangle(0, i32(ry), i32(win_w), i32(SEARCH_BAR_H), SEARCH_BG)
            rlbl := "Repl: "; rlc := strings.clone_to_cstring(rlbl, context.temp_allocator)
            rl.DrawTextEx(ed.font, rlc, {8, f32(ry)+4}, FONT_SIZE, spacing, STATUS_FG)
            rt := string(buf.find.replace_buf[:buf.find.replace_len]); rtc := strings.clone_to_cstring(rt, context.temp_allocator)
            rfc := TEXT_COLOR if buf.find.focus_replace else rl.Color{150,150,150,255}
            rl.DrawTextEx(ed.font, rtc, {fx, f32(ry)+4}, FONT_SIZE, spacing, rfc)
            if buf.find.focus_replace && ed.blink_on { rcx := fx + f32(buf.find.replace_len)*ed.char_width; rl.DrawRectangle(i32(rcx), i32(ry+3), 2, i32(FONT_SIZE), CURSOR_COLOR) }
        }
    }

    // === File browser ===
    if ed.file_browser.visible {
        clicked_path := file_browser_render(&ed.file_browser, ed.font, win_h)
        if len(clicked_path) > 0 {
            tab_open_file(&ed.tab_bar, clicked_path)
        }
    }

    // === Quick open overlay ===
    quick_open_render(&ed.quick_open, ed.font, ed.char_width, win_w, win_h)

    // === Side panel ===
    if ed.side_panel.visible {
        sp_result := side_panel_render(&ed.side_panel, ed.font, ed.char_width, win_w, win_h)
        #partial switch sp_result.action {
        case .Accept_Draft:
            // Insert draft text at cursor
            if ed.side_panel.draft_ready && len(ed.side_panel.draft_text) > 0 {
                ts := current_time_ms()
                if cursor_has_selection(&buf.cursor) {
                    ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor)
                    op := execute_replace(&buf.pt, ss, se, ed.side_panel.draft_text, buf.cursor.offset, ts)
                    undo_stack_push(&buf.undo_stack, &buf.pt, op)
                    buf.cursor.offset = ss + len(ed.side_panel.draft_text)
                    cursor_clear_selection(&buf.cursor)
                } else {
                    op := execute_insert(&buf.pt, buf.cursor.offset, ed.side_panel.draft_text, buf.cursor.offset, ts)
                    undo_stack_push(&buf.undo_stack, &buf.pt, op)
                    buf.cursor.offset += len(ed.side_panel.draft_text)
                }
                cursor_recompute_line_col(&buf.pt, &buf.cursor)
                buf.cursor.preferred_col = buf.cursor.col
                buf.dirty = true
                side_panel_clear_draft(&ed.side_panel)
                memory_on_draft_accept(&ed.ipc)
            }
        case .Dismiss_Draft:
            side_panel_clear_draft(&ed.side_panel)
            memory_on_draft_dismiss(&ed.ipc)
        case .Jump_To_Issue:
            if sp_result.issue_index >= 0 && sp_result.issue_index < len(ed.side_panel.issues) {
                issue := &ed.side_panel.issues[sp_result.issue_index]
                cursor_move_to_line_col(&buf.pt, &buf.cursor, issue.line, issue.col)
                buf.cursor.preferred_col = buf.cursor.col
                ensure_cursor_visible_buf(ed, buf)
            }
        }
    }

    rl.EndDrawing()
}

// ---------------------------------------------------------------------------
// Render helper procs
// ---------------------------------------------------------------------------
find_matching_bracket :: proc(pt: ^Piece_Table, offset: int) -> int {
    if offset < 0 || offset >= pt.doc_length do return -1
    ch := char_at_offset(pt, offset)

    open_ch:  u8; close_ch: u8; direction: int

    switch ch {
    case '(': open_ch = '('; close_ch = ')'; direction = 1
    case ')': open_ch = ')'; close_ch = '('; direction = -1
    case '{': open_ch = '{'; close_ch = '}'; direction = 1
    case '}': open_ch = '}'; close_ch = '{'; direction = -1
    case '[': open_ch = '['; close_ch = ']'; direction = 1
    case ']': open_ch = ']'; close_ch = '['; direction = -1
    case: return -1
    }

    depth := 1
    pos := offset + direction
    for pos >= 0 && pos < pt.doc_length {
        c := char_at_offset(pt, pos)
        if c == open_ch do depth += 1
        else if c == close_ch do depth -= 1
        if depth == 0 do return pos
        pos += direction
    }
    return -1
}

draw_line_selection_at :: proc(ed: ^Editor_State, ls: int, le: int, ss: int, se: int, sl: int, left_m: int) {
    hs := max(ls, ss); he := min(le, se)
    if hs >= he && !(ss <= le && se > le) do return
    y := f32(get_text_top() + sl * LINE_HEIGHT)
    if hs < he { cs := hs-ls; ce := he-ls; sx := f32(left_m) + f32(cs)*ed.char_width; w := f32(ce-cs)*ed.char_width; rl.DrawRectangle(i32(sx), i32(y), i32(w), i32(LINE_HEIGHT), SEL_COLOR) }
    if ss <= le && se > le { col := le-ls; sx := f32(left_m) + f32(col)*ed.char_width; rl.DrawRectangle(i32(sx), i32(y), i32(ed.char_width), i32(LINE_HEIGHT), SEL_COLOR) }
}
