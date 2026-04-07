package editor

import rl "vendor:raylib"
import "core:time"
import "core:os"

// ---------------------------------------------------------------------------
// Editor config
// ---------------------------------------------------------------------------
WINDOW_W       :: 1100
WINDOW_H       :: 700
FONT_SIZE      :: 20
LINE_HEIGHT    :: 24
GUTTER_W       :: 50       // line numbers
TOP_MARGIN     :: 10
CURSOR_BLINK   :: 0.5
SEARCH_BAR_H   :: 28
DOUBLE_CLICK_T :: 0.35
TAB_BAR_H      :: 28
TAB_W          :: 140
MINIMAP_W      :: 60
MINIMAP_CHAR_W :: 1.5
MINIMAP_LINE_H :: 3

// Colors
BG_COLOR         :: rl.Color{30,  30,  30,  255}
TEXT_COLOR        :: rl.Color{212, 212, 212, 255}
CURSOR_COLOR     :: rl.Color{220, 220, 170, 255}
LINENUM_COLOR    :: rl.Color{100, 100, 100, 255}
SEL_COLOR        :: rl.Color{60,  90,  150, 120}
STATUS_BG        :: rl.Color{50,  50,  50,  255}
STATUS_FG        :: rl.Color{180, 180, 180, 255}
CURLINE_COLOR    :: rl.Color{40,  40,  40,  255}
STATUS_WARN_BG   :: rl.Color{120, 60,  30,  255}
SEARCH_BG        :: rl.Color{45,  45,  45,  255}
SEARCH_BORDER    :: rl.Color{80,  80,  80,  255}
SEARCH_MATCH_CLR :: rl.Color{180, 140, 50,  80}
SEARCH_CUR_MATCH :: rl.Color{220, 170, 60,  120}
TAB_BG           :: rl.Color{40,  40,  40,  255}
TAB_ACTIVE_BG    :: rl.Color{30,  30,  30,  255}
TAB_BORDER       :: rl.Color{60,  60,  60,  255}
TAB_TEXT         :: rl.Color{160, 160, 160, 255}
TAB_ACTIVE_TEXT  :: rl.Color{220, 220, 220, 255}
BRACKET_HL       :: rl.Color{255, 255, 100, 60}
MINIMAP_BG       :: rl.Color{25,  25,  25,  255}
MINIMAP_VIEW     :: rl.Color{80,  80,  80,  40}
MINIMAP_TEXT     :: rl.Color{120, 120, 120, 180}

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
    base := GUTTER_W
    if ed.file_browser.visible do base += SIDEBAR_W
    return base
}

// Compute text area right edge (minus optional minimap)
get_right_margin :: proc(ed: ^Editor_State) -> int {
    w := int(rl.GetScreenWidth())
    if ed.show_minimap do w -= MINIMAP_W
    if ed.side_panel.visible do w -= SIDE_PANEL_W
    return w
}

get_text_top :: proc() -> int {
    return TOP_MARGIN + TAB_BAR_H
}

// ---------------------------------------------------------------------------
main :: proc() {
    rl.SetConfigFlags({.WINDOW_RESIZABLE})
    rl.InitWindow(WINDOW_W, WINDOW_H, "Odin Editor")
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

// ---------------------------------------------------------------------------
editor_init :: proc(ed: ^Editor_State) {
    ed.font = load_editor_font()
    rl.SetTextureFilter(ed.font.texture, .BILINEAR)

    m := rl.MeasureTextEx(ed.font, "M", FONT_SIZE, 0)
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
    font := rl.LoadFontEx("fonts/consola.ttf", FONT_SIZE, nil, 256)
    if font.texture.id != 0 do return font

    // Tier 2: platform system fonts
    when ODIN_OS == .Windows {
        sys_paths := [?]cstring{
            "C:\\Windows\\Fonts\\consola.ttf",
            "C:\\Windows\\Fonts\\cour.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, FONT_SIZE, nil, 256)
            if font.texture.id != 0 do return font
        }
    } else when ODIN_OS == .Linux {
        sys_paths := [?]cstring{
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, FONT_SIZE, nil, 256)
            if font.texture.id != 0 do return font
        }
    } else when ODIN_OS == .Darwin {
        sys_paths := [?]cstring{
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFMono-Regular.otf",
            "/Library/Fonts/Courier New.ttf",
        }
        for p in sys_paths {
            font = rl.LoadFontEx(p, FONT_SIZE, nil, 256)
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
    bottom := LINE_HEIGHT
    buf := tab_active_buf(&ed.tab_bar)
    if buf.find.active {
        bottom += SEARCH_BAR_H
        if buf.find.show_replace do bottom += SEARCH_BAR_H
    }
    return (h - get_text_top() - bottom) / LINE_HEIGHT
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
