package editor

import rl "vendor:raylib"
import "core:time"
import "core:os"

DOCS_DIR :: "documents"

// ---------------------------------------------------------------------------
// Editor state (now wraps tab bar)
// ---------------------------------------------------------------------------
Editor_State :: struct {
    tab_bar:       Tab_Bar,
    font:          rl.Font,
    char_width:    f32,
    blink_timer:   f64,
    blink_on:      bool,
    close_blocked: bool,
    warn_timer:    f64,
    // Mouse
    is_dragging:     bool,
    click_count:     int,
    last_click_time: f64,
    last_click_x:    int,
    last_click_y:    int,
    // Panels
    file_browser:  File_Browser,
    quick_open:    Quick_Open,
    show_minimap:  bool,
    // IPC
    ipc:           IPC_Connection,
    // Side panel
    side_panel:    Side_Panel,
}

// Compute dynamic left margin (gutter + optional sidebar)
get_left_margin :: proc(ed: ^Editor_State) -> int {
    base := cfg.gutter_w
    if ed.file_browser.visible do base += cfg.sidebar_w
    return base
}

// Compute text area right edge (minus optional minimap)
get_right_margin :: proc(ed: ^Editor_State) -> int {
    w := int(rl.GetScreenWidth())
    if ed.show_minimap do w -= cfg.minimap_w
    if ed.side_panel.visible do w -= ed.side_panel.width
    return w
}

get_text_top :: proc() -> int {
    return cfg.top_margin + cfg.tab_bar_h
}

// ---------------------------------------------------------------------------
main :: proc() {
    cfg = config_load("editor.conf")

    rl.SetConfigFlags({.WINDOW_RESIZABLE})
    rl.InitWindow(i32(cfg.window_w), i32(cfg.window_h), "Odin Editor")
    rl.SetTargetFPS(60)
    rl.SetExitKey(.KEY_NULL)

    ed: Editor_State
    editor_init(&ed)
    defer editor_destroy(&ed)

    for {
        if rl.WindowShouldClose() {
            // Check if any tab is dirty
            any_dirty := false
            for i := 0; i < len(ed.tab_bar.tabs); i += 1 {
                if ed.tab_bar.tabs[i].dirty { any_dirty = true; break }
            }
            if any_dirty {
                ed.close_blocked = true
                ed.warn_timer = 2.0
            } else {
                break
            }
        }
        editor_update(&ed)
        editor_render(&ed)
    }
    rl.CloseWindow()
}

// ---------------------------------------------------------------------------// ---------------------------------------------------------------------------\n    editor_update :: proc(ed: ^Editor_State) {\n        if ed.warn_timer > 0 do ed.warn_timer -= current_time_ms()\n        tab_bar_update(&ed.tab_bar)\n        file_browser_update(&ed.file_browser, &ed.ipc)\n        quick_open_update(&ed.quick_open)\n\n        for i := 0; i < len(ed.tab_bar.tabs); i += 1 {\n            if !tab_active_is_visible(ed.tab_bar.tabs[i]) do continue\n            tab_active_editor_update(&ed.tab_bar.tabs[i], ed.show_minimap, &ed.ipc)\n        }\n\n        // Blink cursor logic\n        if ed.blink_on && (current_time_ms() - ed.blink_timer) > CURSOR_BLINK * 1000 {\n            ed.blink_on = !ed.blink_on\n            ed.blink_timer = current_time_ms()\n        }\n    }\n\n    editor_render :: proc(ed: ^Editor_State) {\n        rl.BeginDrawing()\n\n        // Background color\n        rl.SetRenderDrawColor(BG_COLOR)\n        rl.ClearBackground()\n\n        // Draw status bar (if any)\n        if !ed.tab_bar.tabs.empty() do draw_status_bar(&ed)\n\n        // Render tabs and side panel\n        tab_bar_render(&ed.tab_bar, get_left_margin(ed))\n\n        // Render file browser sidebar if visible\n        if ed.file_browser.visible {\n            rl.PushStyleColor(rl.ColorTag.Border, TAB_BORDER)\n            rl.DrawRectangleV(Rect{.x = 0, .y = TOP_MARGIN + TAB_BAR_H,\n                                  .w = SIDEBAR_W, .h = int(rl.GetScreenHeight()) - TOP_MARGIN - TAB_BAR_H},\n                              RL_WHITE)\n            tab_bar_render(&ed.tab_bar, get_left_margin(ed) + SIDEBAR_W)\n\n            // Render minimap if enabled\n            if ed.show_minimap {\n                rl.PushStyleColor(rl.ColorTag.Border, MINIMAP_BORDER)\n                rl.DrawRectangleV(Rect{.x = int(rl.GetScreenWidth()) - MINIMAP_W,\n                                      .y = TOP_MARGIN + TAB_BAR_H,\n                                      .w = MINIMAP_W, .h = int(rl.GetScreenHeight()) - TOP_MARGIN - TAB_BAR_H},\n                                  RL_WHITE)\n\n                // Render minimap content\n                rl.PushStyleColor(rl.ColorTag.Text, MINIMAP_TEXT)\n                for i := 0; i < len(ed.tab_bar.tabs); i += 1 {\n                    if !tab_active_is_visible(ed.tab_bar.tabs[i]) do continue\n\n                    tab_active_minimap_render(&ed.tab_bar.tabs[i], ed.show_minimap,\n                                              get_left_margin(ed) + SIDEBAR_W, TOP_MARGIN + TAB_BAR_H)\n                }\n            } else {\n                rl.PopStyleColor()\n            }\n\n            rl.PopStyleColor()\n        }\n\n        // Render text area\n        tab_active_text_area_render(&ed.tab_bar.tabs[0], ed.show_minimap)\n\n        if !tab_active_is_visible(ed.tab_bar.tabs[0]) do return\n\n        editor_draw_cursor(&ed.tab_bar.tabs[0])\n\n        rl.EndDrawing()\n\n    }
editor_init :: proc(ed: ^Editor_State) {
    ed.font = load_editor_font()
    rl.SetTextureFilter(ed.font.texture, .BILINEAR)

    m := rl.MeasureTextEx(ed.font, "M", f32(cfg.font_size), 0)
    ed.char_width = m.x

    tab_bar_init(&ed.tab_bar)

    args := os.args
    if len(args) > 1 {
        tab_open_file(&ed.tab_bar, args[1])
    } else {
        tab_new(&ed.tab_bar)
    }

    ed.blink_timer   = 0
    ed.blink_on      = true
    ed.close_blocked = false
    ed.warn_timer    = 0
    ed.show_minimap  = false

    file_browser_init(&ed.file_browser, ".")
    quick_open_init(&ed.quick_open)
    // Initialize IPC and spawn the Python memory server
    ipc_init(&ed.ipc, "localhost", 9999)
    if spawn_server(&ed.ipc) {
        ipc_connect(&ed.ipc)
    }

    side_panel_init(&ed.side_panel)
}

editor_destroy :: proc(ed: ^Editor_State) {
    rl.UnloadFont(ed.font)
    tab_bar_destroy(&ed.tab_bar)
    file_browser_destroy(&ed.file_browser)
    quick_open_destroy(&ed.quick_open)
    ipc_destroy(&ed.ipc)
    side_panel_destroy(&ed.side_panel)
}

// ---------------------------------------------------------------------------
// Font loading — tries bundled, then system, then raylib default
// ---------------------------------------------------------------------------
load_editor_font :: proc() -> rl.Font {
    // Tier 1: bundled font relative to executable
    font := rl.LoadFontEx("fonts/consola.ttf", i32(cfg.font_size), nil, 256)
    if font.texture.id != 0 do return font

    // Tier 2: platform system fonts
    when ODIN_OS == .Windows {
        sys_paths := [?]cstring{
            "C:\\Windows\\Fonts\\consola.ttf",
            "C:\\Windows\\Fonts\\cour.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, i32(cfg.font_size), nil, 256)
            if font.texture.id != 0 do return font
        }
    } else when ODIN_OS == .Linux {
        sys_paths := [?]cstring{
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, i32(cfg.font_size), nil, 256)
            if font.texture.id != 0 do return font
        }
    } else when ODIN_OS == .Darwin {
        sys_paths := [?]cstring{
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFMono-Regular.otf",
            "/Library/Fonts/Courier New.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, i32(cfg.font_size), nil, 256)
            if font.texture.id != 0 do return font
        }
    }

    // Tier 3: raylib default
    return rl.GetFontDefault()
}

// ---------------------------------------------------------------------------
// Shared utilities
// ---------------------------------------------------------------------------
current_time_ms :: proc() -> i64 {
    t := time.now()
    return i64(time.time_to_unix_nano(t) / 1_000_000)
}

get_visible_lines :: proc(ed: ^Editor_State) -> int {
    h := int(rl.GetScreenHeight())
    bottom := cfg.line_height
    buf := tab_active_buf(&ed.tab_bar)
    if buf.find.active {
        bottom += cfg.search_bar_h
        if buf.find.show_replace do bottom += cfg.search_bar_h
    }
    return (h - get_text_top() - bottom) / cfg.line_height
}

get_max_scroll :: proc(ed: ^Editor_State) -> int {
    buf := tab_active_buf(&ed.tab_bar)
    visible := get_visible_lines(ed)
    return max(0, buf.pt.total_lines - 1 + visible / 2)
}

editor_piece_bytes :: proc(ed: ^Editor_State, p: ^Piece) -> []u8 {
    buf := tab_active_buf(&ed.tab_bar)
    switch p.buffer { case .Original: return buf.pt.original_buf[p.start:p.start+p.length]; case .Add: return buf.pt.add_buf[p.start:p.start+p.length] }; return nil
}

encode_utf8 :: proc(buf: []u8, cp: rune) -> int {
    c := u32(cp)
    if c < 0x80 { buf[0] = u8(c); return 1 }
    if c < 0x800 { buf[0] = u8(0xC0|(c>>6)); buf[1] = u8(0x80|(c&0x3F)); return 2 }
    if c < 0x10000 { buf[0] = u8(0xE0|(c>>12)); buf[1] = u8(0x80|((c>>6)&0x3F)); buf[2] = u8(0x80|(c&0x3F)); return 3 }
    buf[0] = u8(0xF0|(c>>18)); buf[1] = u8(0x80|((c>>12)&0x3F)); buf[2] = u8(0x80|((c>>6)&0x3F)); buf[3] = u8(0x80|(c&0x3F)); return 4
}
