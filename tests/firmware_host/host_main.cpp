// Execute the actual sketch with simulated clock/pins. NO hydraulic model:
// commanding an angle does not establish real outlet position or water shutoff.
#include <iostream>
#include "Arduino.h"
#include "EEPROM.h"
unsigned long g_ms=0;
int g_pin[32]={0},g_servo_angle[32]={0};
bool g_servo_attached[32]={false};
uint8_t g_eeprom[1024];
HostSerial Serial;
HostEEPROM EEPROM;
void setup();void loop();
int main(){
 memset(g_eeprom,0xFF,sizeof g_eeprom);
 g_pin[2]=HIGH;g_pin[3]=LOW;setup();
 std::string line;
 while(std::getline(std::cin,line)){
  if(line.empty())continue;
  if(line[0]=='~'){
   long count=atol(line.c_str()+1);
   for(long i=0;i<count;i++){g_ms++;loop();}
   std::cout<<Serial.tx<<"~END\n"<<std::flush;Serial.tx.clear();
  }else if(line[0]=='?'){
   std::cout<<"SERVO";
   for(int pin:{9,10,11,6})std::cout<<' '<<g_servo_angle[pin]<<' '<<g_servo_attached[pin];
   std::cout<<"\n~END\n"<<std::flush;
  }else if(line=="!button 1")g_pin[2]=LOW;
  else if(line=="!button 0")g_pin[2]=HIGH;
  else {for(char ch:line)Serial.rx.push_back(ch);Serial.rx.push_back('\n');}
 }
}
