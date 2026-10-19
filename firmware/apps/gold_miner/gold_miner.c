/* Original, asset-free Gold Miner style game for the NXE application ABI. */
#include "nxe_app.h"

#define RGB(r,g,b) ((uint16_t)(((r)>>3)<<11 | ((g)>>2)<<5 | ((b)>>3)))
#define BG RGB(18,25,34)
#define INK RGB(231,229,211)
#define GOLD RGB(249,190,58)
#define MAX_ITEMS 13
enum { TITLE, PLAY, WON, LOST, FINISHED };
enum { SWING, EXTEND, RETRACT };
enum { SMALL, LARGE, ROCK, GEM };

typedef struct Item {
    int32_t x, y, r, kind, active;
} Item;
typedef struct Game {
    uint32_t magic, mode, level, score, goal, time_ms;
    int32_t phase, angle, direction, length_q8, attached;
    uint32_t swing_ms, bombs, paused, rng, frames, previous_buttons;
    Item items[MAX_ITEMS];
} Game;

/* sin/cos of -75..75 degrees, Q10. No FPU, trig or libm at runtime. */
static const int16_t rays[33][2] = {
    {-989,265},{-964,345},{-932,422},{-894,497},{-850,568},
    {-801,636},{-746,702},{-686,760},{-623,812},{-555,861},
    {-483,903},{-408,939},{-330,969},{-249,993},{-167,1010},
    {-84,1021},{0,1024},{84,1021},{167,1010},{249,993},
    {330,969},{408,939},{483,903},{555,861},{623,812},
    {686,760},{746,702},{801,636},{850,568},{894,497},
    {932,422},{964,345},{989,265}
};

/* 5x7 column font. Original UI uses only these uppercase glyphs. */
static const uint8_t font[][5] = {
    {0x3e,0x51,0x49,0x45,0x3e},{0,0x42,0x7f,0x40,0},
    {0x62,0x51,0x49,0x49,0x46},{0x22,0x41,0x49,0x49,0x36},
    {0x18,0x14,0x12,0x7f,0x10},{0x27,0x45,0x45,0x45,0x39},
    {0x3c,0x4a,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},
    {0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1e},
    {0x7e,0x09,0x09,0x09,0x7e},{0x7f,0x49,0x49,0x49,0x36},
    {0x3e,0x41,0x41,0x41,0x22},{0x7f,0x41,0x41,0x22,0x1c},
    {0x7f,0x49,0x49,0x49,0x41},{0x7f,0x09,0x09,0x09,0x01},
    {0x3e,0x41,0x49,0x49,0x7a},{0x7f,0x08,0x08,0x08,0x7f},
    {0,0x41,0x7f,0x41,0},{0x20,0x40,0x41,0x3f,0x01},
    {0x7f,0x08,0x14,0x22,0x41},{0x7f,0x40,0x40,0x40,0x40},
    {0x7f,0x02,0x0c,0x02,0x7f},{0x7f,0x04,0x08,0x10,0x7f},
    {0x3e,0x41,0x41,0x41,0x3e},{0x7f,0x09,0x09,0x09,0x06},
    {0x3e,0x41,0x51,0x21,0x5e},{0x7f,0x09,0x19,0x29,0x46},
    {0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7f,0x01,0x01},
    {0x3f,0x40,0x40,0x40,0x3f},{0x1f,0x20,0x40,0x20,0x1f},
    {0x3f,0x40,0x38,0x40,0x3f},{0x63,0x14,0x08,0x14,0x63},
    {0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43}
};

static void rect(NxeHost *h, int x, int y, int w, int z, uint16_t c) {
    int xx, yy, right = x + w, bottom = y + z;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (right > 320) right = 320;
    if (bottom > 240) bottom = 240;
    for (yy = y; yy < bottom; ++yy)
        for (xx = x; xx < right; ++xx) h->pixels[yy * h->stride + xx] = c;
}
static void dot(NxeHost *h, int x, int y, uint16_t c) {
    if (x >= 0 && x < 320 && y >= 0 && y < 240) h->pixels[y * h->stride + x] = c;
}
static int abs_i(int n) { return n < 0 ? -n : n; }
static void line(NxeHost *h, int x, int y, int xx, int yy, uint16_t c) {
    int dx = abs_i(xx-x), sx = x < xx ? 1 : -1;
    int dy = -abs_i(yy-y), sy = y < yy ? 1 : -1, e = dx+dy, e2;
    for (;;) {
        dot(h,x,y,c);
        if (x == xx && y == yy) break;
        e2 = e * 2;
        if (e2 >= dy) { e += dy; x += sx; }
        if (e2 <= dx) { e += dx; y += sy; }
    }
}
static void disc(NxeHost *h, int x, int y, int r, uint16_t c) {
    int dx, dy;
    for (dy = -r; dy <= r; ++dy)
        for (dx = -r; dx <= r; ++dx)
            if (dx*dx + dy*dy <= r*r) dot(h, x+dx, y+dy, c);
}
static void text(NxeHost *h, int x, int y, const char *s, int scale, uint16_t c) {
    int col, row, glyph;
    while (*s) {
        glyph = *s >= '0' && *s <= '9' ? *s - '0' :
                (*s >= 'A' && *s <= 'Z' ? *s - 'A' + 10 : -1);
        if (glyph >= 0)
            for (col = 0; col < 5; ++col)
                for (row = 0; row < 7; ++row)
                    if (font[glyph][col] & (1u << row))
                        rect(h,x + col*scale,y + row*scale,scale,scale,c);
        x += 6*scale; ++s;
    }
}
static void number(NxeHost *h, int x, int y, uint32_t value, uint16_t c) {
    char s[11], reversed[11]; uint32_t n = 0, i;
    do { reversed[n++] = (char)('0' + value % 10); value /= 10; } while (value && n < 10);
    for (i = 0; i < n; ++i) s[i] = reversed[n-i-1];
    s[n] = 0; text(h,x,y,s,1,c);
}
static uint32_t random_u(Game *g) {
    uint32_t v = g->rng;
    v ^= v << 13; v ^= v >> 17; v ^= v << 5;
    g->rng = v; return v;
}
static void stage(Game *g) {
    int i;
    g->goal = 650u + (g->level-1u)*850u;
    g->time_ms = 60000; g->phase = SWING; g->angle = 16;
    g->direction = 1; g->length_q8 = 12*256; g->attached = -1;
    g->swing_ms = 0; g->bombs = 2; g->paused = 0;
    for (i = 0; i < MAX_ITEMS; ++i) {
        Item *p = &g->items[i];
        p->x = 32 + (i % 6)*51 + (int)(random_u(g)%11) - 5;
        p->y = 104 + (i / 6)*55 + (int)(random_u(g)%9) - 4;
        p->kind = i % 4;
        p->r = p->kind == LARGE ? 14 : (p->kind == ROCK ? 12 : 7);
        p->active = 1;
    }
    g->mode = PLAY;
}
static void hook_xy(Game *g, int *x, int *y) {
    int length = g->length_q8 >> 8;
    *x = 160 + (rays[g->angle][0] * length) / 1024;
    *y = 56 + (rays[g->angle][1] * length) / 1024;
}
static uint32_t worth(int kind) {
    return kind == GEM ? 500u : (kind == LARGE ? 350u : (kind == ROCK ? 15u : 85u));
}
static void update(Game *g, uint32_t pressed, uint32_t dt) {
    int x,y,i,speed;
    if (pressed & NXE_PAUSE) g->paused = !g->paused;
    if (g->paused) return;
    if (dt > g->time_ms) dt = g->time_ms;
    g->time_ms -= dt;
    if ((pressed & NXE_FIRE) && g->phase == SWING) g->phase = EXTEND;
    if ((pressed & NXE_BOMB) && g->phase == RETRACT && g->attached >= 0 && g->bombs) {
        g->items[g->attached].active = 0; g->attached = -1; --g->bombs;
    }
    if (g->phase == SWING) {
        g->swing_ms += dt;
        while (g->swing_ms >= 40) {
            g->swing_ms -= 40;
            g->angle += g->direction;
            if (g->angle >= 32) { g->angle = 32; g->direction = -1; }
            if (g->angle <= 0) { g->angle = 0; g->direction = 1; }
        }
    } else {
        /* Substeps prevent a low frame rate from skipping small targets. */
        uint32_t remaining = dt, step;
        while (remaining) {
            step = remaining > 10 ? 10 : remaining;
            remaining -= step;
            if (g->phase == EXTEND) {
                g->length_q8 += (int32_t)(step*150u*256u/1000u);
                hook_xy(g,&x,&y);
                for (i = 0; i < MAX_ITEMS; ++i) {
                    Item *p = &g->items[i];
                    int dx = x-p->x, dy = y-p->y;
                    if (p->active && dx*dx + dy*dy <= (p->r+3)*(p->r+3)) {
                        g->attached = i; g->phase = RETRACT; break;
                    }
                }
                if (x <= 4 || x >= 315 || y >= 227 || g->length_q8 >= 245*256)
                    g->phase = RETRACT;
            } else {
                speed = 200;
                if (g->attached >= 0) {
                    int kind = g->items[g->attached].kind;
                    speed = kind == LARGE ? 36 : (kind == ROCK ? 42 : 110);
                }
                g->length_q8 -= (int32_t)(step*(uint32_t)speed*256u/1000u);
                if (g->length_q8 <= 12*256) {
                    g->length_q8 = 12*256;
                    if (g->attached >= 0) {
                        Item *p = &g->items[g->attached];
                        g->score += worth(p->kind); p->active = 0; g->attached = -1;
                    }
                    g->phase = SWING; break;
                }
            }
        }
    }
    if (g->score >= g->goal) g->mode = g->level == 5 ? FINISHED : WON;
    else if (!g->time_ms) g->mode = LOST;
}
static void item_draw(NxeHost *h, int x, int y, const Item *p) {
    uint16_t c = p->kind == ROCK ? RGB(113,125,137) : GOLD;
    if (p->kind == GEM) {
        int j;
        for (j = -7; j <= 7; ++j) rect(h,x-7+abs_i(j),y+j,15-2*abs_i(j),1,RGB(86,221,221));
        line(h,x-4,y-2,x+2,y-5,INK);
    } else {
        disc(h,x,y,p->r,c);
        line(h,x-p->r/2,y-2,x,y-p->r/2,INK);
        if (p->kind == ROCK) line(h,x+2,y-4,x+5,y+3,RGB(67,79,91));
    }
}
static void scene(NxeHost *h, Game *g) {
    int i,x,y;
    rect(h,0,0,320,240,BG);
    rect(h,0,28,320,35,RGB(50,59,57));
    rect(h,0,63,320,177,RGB(49,35,30));
    for (i = 0; i < 9; ++i) {
        line(h,0,73+i*20,319,78+i*20,RGB(61,43,33));
        rect(h,12+i*37,85+(i%3)*46,4,2,RGB(100,70,41));
    }
    text(h,8,6,"GOLD",1,GOLD); number(h,40,6,g->score,INK);
    text(h,110,6,"GOAL",1,GOLD); number(h,143,6,g->goal,INK);
    text(h,222,6,"TIME",1,GOLD); number(h,256,6,(g->time_ms+999)/1000,INK);
    text(h,8,18,"LV",1,INK); number(h,26,18,g->level,INK);
    text(h,252,18,"BOMB",1,INK); number(h,285,18,g->bombs,GOLD);
    disc(h,160,40,7,RGB(227,169,111));
    rect(h,150,43,20,13,RGB(62,118,143));
    rect(h,151,31,18,5,GOLD); rect(h,147,35,26,3,GOLD);
    rect(h,140,56,40,4,RGB(161,122,72));
    for (i = 0; i < MAX_ITEMS; ++i)
        if (g->items[i].active && i != g->attached)
            item_draw(h,g->items[i].x,g->items[i].y,&g->items[i]);
    hook_xy(g,&x,&y);
    line(h,160,56,x,y,RGB(211,193,152));
    if (g->attached >= 0) item_draw(h,x,y,&g->items[g->attached]);
    else { line(h,x-4,y-2,x-2,y+4,INK); line(h,x+4,y-2,x+2,y+4,INK); }
    rect(h,0,230,320,10,BG);
    text(h,12,232,"FIRE  DROP    UP  BOMB    P  PAUSE",1,INK);
}
static void panel(NxeHost *h, const char *title, const char *detail) {
    rect(h,24,78,272,101,RGB(10,18,25));
    rect(h,24,78,272,2,GOLD); rect(h,24,177,272,2,GOLD);
    text(h,40,93,title,2,GOLD);
    text(h,40,121,detail,1,INK);
    text(h,40,149,"FIRE TO CONTINUE",1,GOLD);
    text(h,40,162,"ESC TO EXIT",1,INK);
}

int nxe_app_entry(NxeHost *h) {
    Game *g;
    uint32_t i, pressed, dt;
    if (!h || h->abi != NXE_ABI || h->struct_bytes < sizeof(NxeHost) ||
        !h->pixels || !h->state || ((uintptr_t)h->pixels & 1) ||
        ((uintptr_t)h->state & 3) || h->width != 320 || h->height != 240 ||
        h->stride < 320 || h->stride > 2048 || h->state_bytes < sizeof(Game))
        return NXE_BAD_HOST;
    g = h->state;
    if (h->event == NXE_STOP) return NXE_EXIT;
    if (h->event == NXE_INIT) {
        for (i = 0; i < sizeof(Game); ++i) ((uint8_t *)g)[i] = 0;
        g->magic = 0x314d474e; g->level = 1;
        g->rng = h->seed ? h->seed : 0x4e584531u;
        stage(g); g->mode = TITLE;
    } else if (h->event != NXE_FRAME || g->magic != 0x314d474e) return NXE_BAD_HOST;
    pressed = h->pressed & ~g->previous_buttons;
    g->previous_buttons = h->buttons;
    if (pressed & NXE_BACK) return NXE_EXIT;
    dt = h->elapsed_ms > 100 ? 100 : h->elapsed_ms;
    if (g->mode == TITLE && (pressed & NXE_FIRE)) g->mode = PLAY;
    else if ((g->mode == WON || g->mode == LOST || g->mode == FINISHED) && (pressed & NXE_FIRE)) {
        if (g->mode == WON) ++g->level;
        else { g->level = 1; g->score = 0; }
        stage(g);
    } else if (g->mode == PLAY) update(g,pressed,dt);
    ++g->frames;
    scene(h,g);
    if (g->mode == TITLE) panel(h,"GOLD MINER","CATCH GOLD AND REACH THE GOAL");
    else if (g->mode == WON) panel(h,"LEVEL CLEAR","YOUR GOLD CARRIES FORWARD");
    else if (g->mode == LOST) panel(h,"TIME IS UP","FIRE TO START A NEW GAME");
    else if (g->mode == FINISHED) panel(h,"GREAT MINER","ALL FIVE LEVELS COMPLETE");
    else if (g->paused) panel(h,"PAUSED","PRESS P TO RESUME");
    return NXE_CONTINUE;
}
