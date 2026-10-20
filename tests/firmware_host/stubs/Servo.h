#pragma once
#include "Arduino.h"
extern int g_servo_angle[32];       // indexed by pin
extern bool g_servo_attached[32];   // false = no pulses; real hose position under load is unknown
class Servo {
  int pin_ = -1, angle_ = 90;
 public:
  uint8_t attach(int pin) { pin_ = pin; g_servo_angle[pin] = angle_; g_servo_attached[pin] = true; return 1; }
  void detach() { if (pin_ >= 0) g_servo_attached[pin_] = false; }
  void write(int a) { angle_ = a; if (pin_ >= 0) g_servo_angle[pin_] = a; }
  int read() { return angle_; }
};
