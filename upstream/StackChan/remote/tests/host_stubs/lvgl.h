#pragma once
typedef int lv_obj_t;
typedef int lv_disp_t;
#define LV_SCR_LOAD_ANIM_MOVE_LEFT 0
#define LV_IMG_DECLARE(name) static int name
#define LV_OBJ_FLAG_SCROLLABLE 0
#define LV_ALIGN_TOP_MID 0
#define LV_ALIGN_TOP_LEFT 0
#define LV_ALIGN_BOTTOM_MID 0
#define LV_PART_MAIN 0
#define LV_OPA_COVER 0
#define LV_SYMBOL_DOWN "down"
static const int lv_font_montserrat_18 = 18;
static inline lv_disp_t *lv_disp_get_default(void) { static int display; return &display; }
static inline lv_obj_t *lv_obj_create(lv_obj_t *parent)
{ (void)parent; static int objects[64]; static unsigned next; return &objects[next++]; }
static inline lv_obj_t *lv_label_create(lv_obj_t *parent) { return lv_obj_create(parent); }
static inline lv_obj_t *lv_dropdown_create(lv_obj_t *parent) { return lv_obj_create(parent); }
static inline void lv_dropdown_set_selected(lv_obj_t *obj, unsigned value) { *obj = (int)value; }
static inline unsigned lv_dropdown_get_selected(lv_obj_t *obj) { return (unsigned)*obj; }
#define lv_obj_clear_flag(...) ((void)0)
#define lv_label_set_text(...) ((void)0)
#define lv_obj_align(...) ((void)0)
#define lv_dropdown_set_options(...) ((void)0)
#define lv_dropdown_set_symbol(...) ((void)0)
#define lv_obj_set_style_bg_color(...) ((void)0)
#define lv_obj_set_style_bg_opa(...) ((void)0)
#define lv_obj_set_style_text_font(...) ((void)0)
#define lv_obj_del(...) ((void)0)
