#pragma once
#include "Arduino.h"
extern uint8_t g_eeprom[1024];
struct HostEEPROM {
  template <typename T> T &get(int addr, T &t) { memcpy(&t, g_eeprom + addr, sizeof(T)); return t; }
  template <typename T> const T &put(int addr, const T &t) { memcpy(g_eeprom + addr, &t, sizeof(T)); return t; }
};
extern HostEEPROM EEPROM;
