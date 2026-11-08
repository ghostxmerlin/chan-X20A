#pragma once
static inline void vTaskDelay(int ticks) { (void)ticks; }
static inline void xTaskCreate(void (*task)(void *), const char *name, int size, void *arg, int priority, void *handle)
{ (void)task; (void)name; (void)size; (void)arg; (void)priority; (void)handle; }
