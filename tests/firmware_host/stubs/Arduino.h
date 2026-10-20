// Host-side stand-in for the Arduino core so water_rig.ino can be compiled and exercised with g++.
// Virtual time, pins and serial are driven by host_main.cpp.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <string>
#include <deque>

typedef uint8_t byte;
class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper *>(s))
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define BIN 2
#define DEC 10
enum { A0 = 14, A1, A2, A3, A4, A5 };

extern unsigned long g_ms;
extern int g_pin[32];
inline unsigned long millis() { return g_ms; }
inline void pinMode(uint8_t, uint8_t) {}
inline int digitalRead(uint8_t p) { return g_pin[p]; }
inline void digitalWrite(uint8_t p, uint8_t v) { g_pin[p] = v; }
inline void delay(unsigned long ms) { g_ms += ms; }
inline unsigned long micros() { return g_ms * 1000UL; }
inline int analogRead(uint8_t) { return 512; }
inline void randomSeed(unsigned long s) { srand((unsigned)s); }
inline long random(long lo, long hi) { return lo + (long)(rand() % (hi - lo)); }
using std::fabs;

class HostSerial {
 public:
  std::deque<char> rx;
  std::string tx;
  void begin(long) {}
  int available() { return (int)rx.size(); }
  int read() {
    if (rx.empty()) return -1;
    char c = rx.front();
    rx.pop_front();
    return c;
  }
  void print(const char *s) { tx += s; }
  void print(const __FlashStringHelper *s) { tx += reinterpret_cast<const char *>(s); }
  void print(char c) { tx += c; }
  void print(int v, int base = DEC) { print((long)v, base); }
  void print(unsigned int v, int base = DEC) { print((unsigned long)v, base); }
  void print(long v, int base = DEC) {
    if (base == BIN) {
      std::string b;
      unsigned long u = (unsigned long)v;
      do { b.insert(b.begin(), char('0' + (u & 1))); u >>= 1; } while (u);
      tx += b;
    } else {
      tx += std::to_string(v);
    }
  }
  void print(unsigned long v, int base = DEC) { print((long)v, base); }
  void print(uint8_t v, int base = DEC) { print((long)v, base); }
  void print(double v, int digits = 2) {
    char b[48];
    snprintf(b, sizeof b, "%.*f", digits, v);
    tx += b;
  }
  template <typename T> void println(T v) { print(v); tx += "\r\n"; }
  template <typename T> void println(T v, int f) { print(v, f); tx += "\r\n"; }
  void println() { tx += "\r\n"; }
};
extern HostSerial Serial;
