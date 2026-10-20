// gangwon-water-ai · 호스 출구 높이 조절 실험 장치 v5
//
// 하는 일
//   * 서보 4개가 호스 끝만 들어 올리거나 내린다. 지지대가 호스 무게를 받친다.
//   * 순서: H1(A→지역1), H2(A→지역3), H3(B→지역3), H4(B→지역2).
//   * ANG으로 실물에 맞춘 공급/차단 각도를 저장하기 전 OPENM은 거절한다.
//   * 힘 센서 3개는 PASCO 인터페이스→PC로 읽는다. 이 Uno는 힘을 측정하지 않는다.
//   * 안전 정지는 PC와 무관하게 여기서 처리한다.
//       - 잠긴 채 부팅: 부팅마다 바뀌는 번호를 "ARM 번호"로 받기 전에는 열지 않는다(재부팅 뒤 조용히 계속되는 것 방지)
//       - 하트비트 5초 끊김 → 전부 닫고 잠금 · STOP 버튼 → 전부 닫고 래치(RESET 필요)
//       - 1회 최대 공급(기본 65초) · 차단 위치에서도 서보 신호를 유지 · 기동을 150 ms씩 어긋나게 함
//   * 차단 각도는 출구가 최대 수면보다 높아야 한다. 소프트웨어는 높이/실제 차단을 검증할 수 없다.
//   * 전원 차단/서보 고장 시 호스 위치는 보장하지 않는다. 별도 수동 차단이 필요하다.
//   * 이전 핀치 밸브 v4에서 프로토콜/비상정지 코드를 가져와 수정했다. 보정값은 승계하지 않는다.
//
// 라이브러리: Servo·EEPROM(기본 포함)만. 직렬 38400 baud, 줄 단위 ASCII(줄 끝 LF).
// 명령: PING · HB · STAT · ARM n · OPENM a b c d · CLOSE 관 · STOP · RESET · POS 관 각도 · ANG 관 열림 닫힘 · LIMIT ms · LOOP 0|1 · CONFIG
// 응답: OK … · ERR 코드 [인자] · DONE 관 공급위치시간ms 이유 · ALARM 이유 · ST ms L=0000 B=0 A=0 HB=0 C=0

#include <Servo.h>
#include <EEPROM.h>

// ---------------------------------------------------------------- 배선
const uint8_t NLINE = 4;
const char *const LINE_ID[NLINE] = {"R1", "R3A", "R3B", "R2"};  // H1 · H2 · H3 · H4(기존 명령 ID 호환)
const uint8_t SERVO_PIN[NLINE] = {9, 10, 11, 6};
const uint8_t LED_LINE_PIN[NLINE] = {4, 5, 7, 12};  // 선택: 관별 표시등(없어도 된다)
const uint8_t BUTTON_PIN = 2;   // STOP 버튼(택트 스위치) → GND. 누르면 LOW
const uint8_t LOOP_PIN = 3;     // 선택: 버튼 케이블 안 점퍼 고리 → GND. 빠지면 HIGH (LOOP 1일 때만 검사)
const uint8_t BUZZER_PIN = 8;   // 능동 버저 +. 다른 쪽은 GND
const uint8_t LED_PIN = 13;     // 어느 관이든 열려 있으면 켜짐(보드 내장 LED)

// ---------------------------------------------------------------- 시간 기준
const unsigned long HB_TIMEOUT_MS = 5000;
const unsigned long MIN_OPEN_MS = 200;
const unsigned long STAGGER_MS = 150;       // 서보 4개가 한꺼번에 기동 전류를 끌지 않게
const unsigned long POS_HOLD_MS = 10000;    // POS로 옮긴 서보는 이 시간 뒤 닫힘으로 돌아간다
const unsigned long STATUS_EVERY_MS = 500;
const unsigned long DEBOUNCE_MS = 30;
const uint8_t MIN_ANG_GAP = 15;

// ---------------------------------------------------------------- 설정(EEPROM)
const uint32_t CONFIG_MAGIC = 0x57523501UL;  // v5: 핀치 밸브의 이전 각도 설정은 무효
struct Config {
  uint32_t magic;
  uint8_t openDeg[NLINE];
  uint8_t closeDeg[NLINE];
  uint32_t maxOpenMs;
  uint8_t loopCheck;
  uint8_t calibrated;
};
Config cfg;

void defaultConfig() {
  cfg.magic = CONFIG_MAGIC;
  for (uint8_t i = 0; i < NLINE; i++) {
    cfg.openDeg[i] = 90;   // 미보정 값으로는 서보를 움직이지 않는다
    cfg.closeDeg[i] = 90;
  }
  cfg.maxOpenMs = 65000UL;
  cfg.loopCheck = 0;
  cfg.calibrated = 0;
}

void saveConfig() { EEPROM.put(0, cfg); }

// ---------------------------------------------------------------- 상태
Servo valve[NLINE];
bool attached[NLINE];
bool active[NLINE];       // OPENM으로 열려 있음(또는 곧 열림)
bool started[NLINE];      // 서보에 열림 각도를 썼음
unsigned long startAt[NLINE];
unsigned long openAt[NLINE];
unsigned long openFor[NLINE];
unsigned long closedAt[NLINE];
bool posHeld[NLINE];
unsigned long posAt[NLINE];
bool batch = false;       // OPENM 한 묶음이 진행 중

unsigned long lastHb = 0;
unsigned long lastStatus = 0;
bool latched = false;     // STOP 버튼·케이블로 걸린 정지. RESET으로만 푼다
bool armed = false;       // ARM 번호를 받았는가
uint16_t nonce = 0;
unsigned long buttonLowSince = 0;

// 버저: 짧게 n번(주기 끝) · 길게 n번(오류) · 계속(비상정지, RESET까지)
uint8_t beepsLeft = 0;
unsigned long beepOnMs = 0, beepOffMs = 0, beepAt = 0;
bool beepOn = false;

char buf[48];
uint8_t blen = 0;
bool discardLine = false;

// ---------------------------------------------------------------- 보조
int8_t findLine(const char *s) {
  for (uint8_t i = 0; i < NLINE; i++)
    if (strcmp(s, LINE_ID[i]) == 0) return i;
  return -1;
}

bool anyActive() {
  for (uint8_t i = 0; i < NLINE; i++)
    if (active[i]) return true;
  return false;
}

bool buttonPressed() { return digitalRead(BUTTON_PIN) == LOW; }
bool cableBroken() { return cfg.loopCheck && digitalRead(LOOP_PIN) == HIGH; }

void beep(uint8_t n, unsigned long onMs, unsigned long offMs) {
  beepsLeft = n;
  beepOnMs = onMs;
  beepOffMs = offMs;
  beepOn = false;
  beepAt = millis() - offMs;  // 바로 시작
}

// 숫자만으로 된 인자를 읽는다. 다른 글자가 섞이면 false
bool parseUL(const char *s, unsigned long &out) {
  if (!s || !*s) return false;
  char *end = nullptr;
  out = strtoul(s, &end, 10);
  return end && *end == 0 && s[0] >= '0' && s[0] <= '9';
}

void err(const __FlashStringHelper *msg, const char *arg = nullptr) {
  Serial.print(F("ERR "));
  Serial.print(msg);
  if (arg) {
    Serial.print(' ');
    Serial.print(arg);
  }
  Serial.println();
}

void attachAt(uint8_t i, uint8_t deg) {
  if (!attached[i]) {
    valve[i].write(deg);  // attach 전에 각도를 정해 두어 튀지 않게
    valve[i].attach(SERVO_PIN[i]);
    attached[i] = true;
  }
  valve[i].write(deg);
}

void moveClosed(uint8_t i) {
  if (!(cfg.calibrated & (1 << i))) return;
  attachAt(i, cfg.closeDeg[i]);
  closedAt[i] = millis();
  posHeld[i] = false;
}

void closeLine(uint8_t i, const __FlashStringHelper *reason) {
  moveClosed(i);
  digitalWrite(LED_LINE_PIN[i], LOW);
  if (!active[i]) return;
  active[i] = false;
  Serial.print(F("DONE "));
  Serial.print(LINE_ID[i]);
  Serial.print(' ');
  Serial.print(started[i] ? millis() - openAt[i] : 0UL);
  Serial.print(' ');
  Serial.println(reason);
  started[i] = false;
}

void closeAll(const __FlashStringHelper *reason) {
  for (uint8_t i = 0; i < NLINE; i++) {
    closeLine(i, reason);
    if (posHeld[i]) moveClosed(i);
  }
  batch = false;
}

void fault(const __FlashStringHelper *reason, bool latch) {
  closeAll(reason);
  armed = false;
  if (latch) latched = true;
  Serial.print(F("ALARM "));
  Serial.println(reason);
  if (latch) beep(255, 400, 200);  // 계속(RESET까지)
  else beep(3, 600, 300);         // 길게 3번
}

void printStatus() {
  Serial.print(F("ST "));
  Serial.print(millis());
  Serial.print(F(" L="));
  for (uint8_t i = 0; i < NLINE; i++) Serial.print(active[i] ? '1' : '0');
  Serial.print(F(" B="));
  Serial.print(latched ? 1 : 0);
  Serial.print(F(" A="));
  Serial.print(armed ? 1 : 0);
  Serial.print(F(" HB="));
  Serial.print(millis() - lastHb < HB_TIMEOUT_MS ? 1 : 0);
  Serial.print(F(" C="));
  Serial.println(cfg.calibrated);
}

// ---------------------------------------------------------------- 명령
bool cmdOpenM(char *args[4]) {
  unsigned long ms[NLINE];
  for (uint8_t i = 0; i < NLINE; i++) {
    if (!parseUL(args[i], ms[i]) || (ms[i] != 0 && (ms[i] < MIN_OPEN_MS || ms[i] > cfg.maxOpenMs))) {
      err(F("BAD_MS"), LINE_ID[i]);
      return true;
    }
  }
  if (latched || buttonPressed() || cableBroken()) { err(F("STOP_LATCHED")); return true; }
  if (!armed) { err(F("NOT_ARMED")); return true; }
  for (uint8_t i = 0; i < NLINE; i++)
    if (posHeld[i]) { err(F("BUSY")); return true; }
  for (uint8_t i = 0; i < NLINE; i++)
    if (ms[i] && !(cfg.calibrated & (1 << i))) { err(F("NOT_CALIBRATED"), LINE_ID[i]); return true; }
  for (uint8_t i = 0; i < NLINE; i++)
    if (ms[i] && active[i]) { err(F("LINE_BUSY"), LINE_ID[i]); return true; }
  unsigned long now = millis(), offset = 0;
  bool any = false;
  for (uint8_t i = 0; i < NLINE; i++) {
    if (!ms[i]) continue;
    active[i] = true;
    started[i] = false;
    startAt[i] = now + offset;
    openFor[i] = ms[i];
    offset += STAGGER_MS;
    any = true;
  }
  batch = any;
  Serial.print(F("OK OPENM"));
  for (uint8_t i = 0; i < NLINE; i++) {
    Serial.print(' ');
    Serial.print(ms[i]);  // 받은 값을 되돌려 보내 PC가 글자 손실을 확인한다
  }
  Serial.println();
  return true;
}

// 문법이 맞는 명령이면 true(하트비트로 인정)
bool handle(char *line) {
  char *cmd = strtok(line, " ");
  if (!cmd) return false;
  char *a[5];
  for (uint8_t k = 0; k < 5; k++) a[k] = strtok(nullptr, " ");

  if (strcmp(cmd, "HB") == 0 && !a[0]) return true;
  if (strcmp(cmd, "PING") == 0 && !a[0]) {
    Serial.print(F("OK PONG water_rig v5 "));
    Serial.println(nonce);  // 부팅 번호: 바뀌었으면 그사이 재부팅된 것이다
    return true;
  }
  if (strcmp(cmd, "STAT") == 0 && !a[0]) { printStatus(); return true; }
  if (strcmp(cmd, "STOP") == 0 && !a[0]) {
    closeAll(F("STOP"));
    armed = false;
    Serial.println(F("OK STOP"));
    return true;
  }
  if (strcmp(cmd, "RESET") == 0 && !a[0]) {
    if (buttonPressed()) { err(F("BUTTON_STILL_PRESSED")); return true; }
    if (cableBroken()) { err(F("CABLE_OPEN")); return true; }
    latched = false;
    beepsLeft = 0;
    digitalWrite(BUZZER_PIN, LOW);
    Serial.println(F("OK RESET"));
    return true;
  }
  if (strcmp(cmd, "ARM") == 0 && a[0] && !a[1]) {
    unsigned long n;
    if (!parseUL(a[0], n) || n != nonce) { err(F("BAD_NONCE")); return true; }
    if (latched || buttonPressed() || cableBroken()) { err(F("STOP_LATCHED")); return true; }
    armed = true;
    Serial.println(F("OK ARM"));
    return true;
  }
  if (strcmp(cmd, "OPENM") == 0 && a[3] && !a[4]) return cmdOpenM(a);
  if (strcmp(cmd, "CLOSE") == 0 && a[0] && !a[1]) {
    int8_t i = findLine(a[0]);
    if (i < 0) { err(F("UNKNOWN_LINE")); return true; }
    closeLine(i, F("CLOSE"));
    Serial.print(F("OK CLOSE "));
    Serial.println(LINE_ID[i]);
    return true;
  }
  if (strcmp(cmd, "CONFIG") == 0 && !a[0]) {
    Serial.print(F("CFG v5 limit="));
    Serial.print(cfg.maxOpenMs);
    Serial.print(F(" loop="));
    Serial.print(cfg.loopCheck);
    Serial.print(F(" calibrated="));
    Serial.print(cfg.calibrated);
    for (uint8_t i = 0; i < NLINE; i++) {
      Serial.print(' ');
      Serial.print(LINE_ID[i]);
      Serial.print(':');
      Serial.print(cfg.openDeg[i]);
      Serial.print('/');
      Serial.print(cfg.closeDeg[i]);
    }
    Serial.println();
    return true;
  }
  // 아래 설정·조정 명령은 모든 관이 닫힌 상태에서만 받는다
  bool known = (strcmp(cmd, "POS") == 0 && a[1] && !a[2]) || (strcmp(cmd, "ANG") == 0 && a[2] && !a[3]) ||
               (strcmp(cmd, "LIMIT") == 0 && a[0] && !a[1]) || (strcmp(cmd, "LOOP") == 0 && a[0] && !a[1]);
  if (!known) { err(F("UNKNOWN_COMMAND"), cmd); return false; }
  if (anyActive()) { err(F("BUSY")); return true; }
  if (strcmp(cmd, "POS") == 0) {
    int8_t i = findLine(a[0]);
    unsigned long d;
    if (i < 0 || !parseUL(a[1], d) || d > 180) { err(F("BAD_POS")); return true; }
    if (latched || buttonPressed() || cableBroken()) { err(F("STOP_LATCHED")); return true; }
    if (!armed) { err(F("NOT_ARMED")); return true; }
    if (!(cfg.calibrated & (1 << i))) { err(F("NOT_CALIBRATED"), LINE_ID[i]); return true; }
    attachAt(i, (uint8_t)d);
    posHeld[i] = true;
    posAt[i] = millis();
    Serial.print(F("OK POS "));
    Serial.print(LINE_ID[i]);
    Serial.print(' ');
    Serial.println(d);
    return true;
  }
  if (strcmp(cmd, "ANG") == 0) {
    if (latched || buttonPressed() || cableBroken()) { err(F("STOP_LATCHED")); return true; }
    int8_t i = findLine(a[0]);
    unsigned long o, z;
    if (i < 0 || !parseUL(a[1], o) || !parseUL(a[2], z) || o > 180 || z > 180 ||
        (o > z ? o - z : z - o) < MIN_ANG_GAP) { err(F("BAD_ANG")); return true; }
    cfg.openDeg[i] = (uint8_t)o;
    cfg.closeDeg[i] = (uint8_t)z;
    cfg.calibrated |= (1 << i);
    saveConfig();
    moveClosed(i);
    Serial.print(F("OK ANG "));
    Serial.print(LINE_ID[i]);
    Serial.print(' ');
    Serial.print(o);
    Serial.print(' ');
    Serial.println(z);
    return true;
  }
  if (strcmp(cmd, "LIMIT") == 0) {
    unsigned long ms;
    if (!parseUL(a[0], ms) || ms < 1000 || ms > 120000UL) { err(F("BAD_LIMIT")); return true; }
    cfg.maxOpenMs = ms;
    saveConfig();
    Serial.print(F("OK LIMIT "));
    Serial.println(ms);
    return true;
  }
  unsigned long on;  // LOOP
  if (!parseUL(a[0], on) || on > 1) { err(F("BAD_LOOP")); return true; }
  cfg.loopCheck = (uint8_t)on;
  saveConfig();
  Serial.print(F("OK LOOP "));
  Serial.println(on);
  return true;
}

// ---------------------------------------------------------------- 메인
void setup() {
  EEPROM.get(0, cfg);
  if (cfg.magic != CONFIG_MAGIC) {
    defaultConfig();
    saveConfig();
  }
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LOOP_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  for (uint8_t i = 0; i < NLINE; i++) {
    pinMode(LED_LINE_PIN[i], OUTPUT);
    active[i] = started[i] = attached[i] = posHeld[i] = false;
    moveClosed(i);
  }
  // 부팅 번호: 떠 있는 아날로그 핀 잡음과 시간으로 섞는다(보안용이 아니라 '재부팅 감지'용)
  uint16_t seed = 0;
  for (uint8_t k = 0; k < 8; k++) seed = (uint16_t)(seed * 31 + analogRead(A0 + (k % 6)) + micros());
  randomSeed(seed);
  nonce = (uint16_t)random(1000, 10000);
  Serial.begin(38400);
  Serial.print(F("OK BOOT water_rig v5 "));
  Serial.println(nonce);
  beep(1, 80, 80);
}

void loop() {
  // 1. 직렬 명령(문법이 맞는 명령만 하트비트로 인정)
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (discardLine) {
      if (ch == '\n') discardLine = false;
      continue;
    }
    if (ch == '\n') {
      buf[blen] = 0;
      if (handle(buf)) lastHb = millis();
      blen = 0;
    } else if (blen < sizeof(buf) - 1) {
      buf[blen++] = ch;
    } else {
      blen = 0;
      discardLine = true;
      err(F("LINE_TOO_LONG"));
    }
  }

  unsigned long now = millis();

  // 2. STOP 버튼·케이블 고리(짧은 떨림은 무시)
  if (buttonPressed() || cableBroken()) {
    if (buttonLowSince == 0) buttonLowSince = now ? now : 1;
    if (!latched && now - buttonLowSince >= DEBOUNCE_MS) {
      if (buttonPressed()) fault(F("BUTTON"), true);
      else fault(F("CABLE"), true);
    }
  } else {
    buttonLowSince = 0;
  }

  // 3. 하트비트
  if ((anyActive() || armed) && now - lastHb >= HB_TIMEOUT_MS) {
    bool wasActive = anyActive();
    closeAll(F("HB_LOST"));
    armed = false;
    if (wasActive) {
      Serial.println(F("ALARM HB_LOST"));
      beep(3, 600, 300);
    }
  }

  // 4. 공급/차단 각도로 이동, POS 자동 복귀. 차단 위치 유지에 토크가 필요하므로 detach하지 않는다.
  for (uint8_t i = 0; i < NLINE; i++) {
    if (active[i] && !started[i] && (long)(now - startAt[i]) >= 0) {
      attachAt(i, cfg.openDeg[i]);
      digitalWrite(LED_LINE_PIN[i], HIGH);
      started[i] = true;
      openAt[i] = now;
    }
    if (active[i] && started[i] && now - openAt[i] >= openFor[i]) closeLine(i, F("TIME"));
    if (posHeld[i] && now - posAt[i] >= POS_HOLD_MS) moveClosed(i);
  }
  if (batch && !anyActive()) {
    batch = false;
    beep(1, 150, 100);  // 짧게 1번: 이번 주기 밸브가 모두 닫힘
  }

  // 5. 표시등·버저
  digitalWrite(LED_PIN, anyActive() ? HIGH : LOW);
  if (beepsLeft && now - beepAt >= (beepOn ? beepOnMs : beepOffMs)) {
    beepAt = now;
    beepOn = !beepOn;
    digitalWrite(BUZZER_PIN, beepOn ? HIGH : LOW);
    if (!beepOn && beepsLeft != 255) beepsLeft--;
  }

  // 6. 상태 보고
  if (now - lastStatus >= STATUS_EVERY_MS) {
    lastStatus = now;
    printStatus();
  }
}
