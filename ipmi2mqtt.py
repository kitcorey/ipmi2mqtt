#!/usr/bin/env python3
#import sys
#import signal
import time
import yaml
import json
import pyipmi
import threading
import pyipmi.interfaces
import paho.mqtt.client as pmqtt
from types import SimpleNamespace

threadLock = threading.Lock()

def main():
    config = getConfig()
    registered = False
    threads = []

    #Create/Start Threads
    mqtt = mqttConnect(config)

    mqtt.loop_start()

    while True:
        for device in config.devices:
            thread = deviceThread(config, device, mqtt, registered)
            thread.start()
            threads.append(thread)
        if registered:
            registered = False
        time.sleep(config.ipmi.interval)

    #Wait for all threads just incase
    for thread in threads:
        thread.join()

    print("Done.")

### Functions ###
class deviceThread(threading.Thread):
    def __init__(self, config, device, mqtt, registered):
        threading.Thread.__init__(self)
        self.config = config
        self.device = device
        self.mqtt = mqtt
        self.registered = registered

    def run(self):
        ipmi = ipmiConnect(self.config.ipmi, self.device)
        processDevice(self.config, self.device, ipmi, self.mqtt, self.registered)


def ipmiConnect(auth, device):
    username = ''
    password = ''

    if hasattr(auth, 'username'):
        username = auth.username
    if hasattr(auth, 'password'):
        password = auth.password

    if hasattr(device, "username"):
        username = device.username
    if hasattr(device, "password"):
        password = device.password

    interface = pyipmi.interfaces.create_interface(
        interface='rmcp',
        slave_address=0x81,
        host_target_address=0x20,
        keep_alive_interval=1
    )

    ipmi = pyipmi.create_connection(interface)
    ipmi.session.set_session_type_rmcp(host=device.host, port=623)
    ipmi.session.set_auth_type_user(username=username, password=password)
    ipmi.target = pyipmi.Target(ipmb_address=0x20)
    ipmi.session.establish()
    return ipmi


def processDevice(config, device, ipmi, mqtt, registered):
    power = "ON" if ipmi.get_chassis_status().power_on else "OFF"

    watts = None
    try:
        sensors = ipmi.get_power_reading(1)
        watts = sensors.current_power
    except pyipmi.errors.CompletionCodeError as e:
        if config.output >= 2:
            print(f"Failed to get power reading (no PMBUS?)")

    if not registered:
        fru = ipmi.get_fru_inventory()
        product = fru.product_info_area

        if not (manufacturer := str(product.manufacturer)):
            manufacturer = str(fru.board_info_area.manufacturer)

        if not (part_number := str(product.part_number)):
            part_number = str(fru.board_info_area.part_number)

        serial_number = str(product.serial_number)
        if not serial_number or serial_number.isspace():
            serial_number = str(fru.board_info_area.serial_number)

        mdevice = {
            #"configuration_url": f"https://{device.host}",
            "identifiers": str(serial_number),
            "manufacturer": str(manufacturer),
            "model": str(part_number),
            "name": device.name,
        }
        hassRegister(mdevice, device, mqtt, watts is not None)

    ipmi.session.close()
        
    if config.output >= 2:
        print(f"IPMI: {device.host} is powered {power}"
                + (f" {watts}W)" if watts is not None else ''))

    mqtt.publish(f"ipmi2mqtt/{device.name}/switch/state", power)
    if watts is not None:
        mqtt.publish(f"ipmi2mqtt/{device.name}/watts/state", watts)

def hassRegister(mdevice, device, mqtt, watts_supported):
    payload = {
        "~": f"ipmi2mqtt/{device.name}/switch",
        "name": f"{device.name}_switch",
        "unique_id": f"{device.name}_switch",
        "manufacturer": f"{mdevice['manufacturer']}",
        "identifiers": f"{mdevice['identifiers']}",
        "model": f"{mdevice['model']}",
        "platform": "mqtt",
        "command_topic": "~/set",
        "state_topic": "~/state",
        "device": mdevice,
    }
    topic = f"homeassistant/switch/{device.name}/switch/config"
    threadLock.acquire()
    mqtt.publish(topic, json.dumps(payload))

    payload = {
        "~": f"ipmi2mqtt/{device.name}/soft_shutdown",
        "name": f"{device.name}_soft_shutdown",
        "unique_id": f"{device.name}_soft_shutdown",
        "manufacturer": f"{mdevice['manufacturer']}",
        "identifiers": f"{mdevice['identifiers']}",
        "model": f"{mdevice['model']}",
        "platform": "mqtt",
        "command_topic": "~/press",
        "device": mdevice,
    }
    topic = f"homeassistant/button/{device.name}/soft_shutdown/config"
    mqtt.publish(topic, json.dumps(payload))

    payload = {
        "~": f"ipmi2mqtt/{device.name}/power_cycle",
        "name": f"{device.name}_power_cycle",
        "unique_id": f"{device.name}_power_cycle",
        "manufacturer": f"{mdevice['manufacturer']}",
        "identifiers": f"{mdevice['identifiers']}",
        "model": f"{mdevice['model']}",
        "platform": "mqtt",
        "command_topic": "~/press",
        "device": mdevice,
    }
    topic = f"homeassistant/button/{device.name}/power_cycle/config"
    mqtt.publish(topic, json.dumps(payload))

    payload = {
        "~": f"ipmi2mqtt/{device.name}/hard_reset",
        "name": f"{device.name}_hard_reset",
        "unique_id": f"{device.name}_hard_reset",
        "manufacturer": f"{mdevice['manufacturer']}",
        "identifiers": f"{mdevice['identifiers']}",
        "model": f"{mdevice['model']}",
        "platform": "mqtt",
        "command_topic": "~/press",
        "device": mdevice,
    }
    topic = f"homeassistant/button/{device.name}/hard_reset/config"
    mqtt.publish(topic, json.dumps(payload))

    if watts_supported:
        payload = {
            "~": f"ipmi2mqtt/{device.name}/watts",
            "name": f"{device.name}_watts",
            "unique_id": f"{device.name}_watts",
            "manufacturer": f"{mdevice['manufacturer']}",
            "model": f"{mdevice['model']}",
            "platform": "mqtt",
            "state_topic": "~/state",
            "device": mdevice,
        }
        topic = f"homeassistant/sensor/{device.name}/watts/config"
        mqtt.publish(topic, json.dumps(payload))

    threadLock.release()


#def term(_signo, _stack_frame):
#    m.loop_stop()
#    sys.exit()

class mqttSetHandler:
    def __init__(self, config, device):
        self.config = config
        self.device = device

    def message(self, client, userdata, message): 
        stateTopic = message.topic.replace("/set", "/state")
        value = message.payload.decode("utf-8") 
        if value in ["ON", "OFF"]:
            ipmi = ipmiConnect(self.config.ipmi, self.device)
            if value == "OFF":
                if self.config.output:
                    print(f"Shutting Down {self.device.name}")
                ipmi.chassis_control_power_down()
            elif value == "ON":
                ipmi.chassis_control_power_up()
                if self.config.output:
                    print(f"Powering Up {self.device.name}")
                power = "ON" if ipmi.get_chassis_status().power_on else "OFF"
                client.publish(f"{stateTopic}", power)
            ipmi.session.close()

class mqttSoftShutdownHandler:
    def __init__(self, config, device):
        self.config = config
        self.device = device

    def message(self, client, userdata, message): 
        print(f"Soft Shutdown Handler")
        value = message.payload.decode("utf-8") 
        if value in ["PRESS"]:
            ipmi = ipmiConnect(self.config.ipmi, self.device)
            if self.config.output:
                print(f"Shutting Down {self.device.name}")
            ipmi.chassis_control_soft_shutdown()
            ipmi.session.close()

class mqttPowerCycleHandler:
    def __init__(self, config, device):
        self.config = config
        self.device = device

    def message(self, client, userdata, message): 
        print(f"Soft Shutdown Handler")
        value = message.payload.decode("utf-8") 
        if value in ["PRESS"]:
            ipmi = ipmiConnect(self.config.ipmi, self.device)
            if self.config.output:
                print(f"Power cycling {self.device.name}")
            ipmi.chassis_control_power_cycle()
            ipmi.session.close()

class mqttHardResetHandler:
    def __init__(self, config, device):
        self.config = config
        self.device = device

    def message(self, client, userdata, message): 
        print(f"Hard Reset Handler")
        value = message.payload.decode("utf-8") 
        if value in ["PRESS"]:
            ipmi = ipmiConnect(self.config.ipmi, self.device)
            if self.config.output:
                print(f"Resetting {self.device.name}")
            ipmi.chassis_control_hard_reset()
            ipmi.session.close()


def mqttConnect(config):
    mqtt = pmqtt.Client(pmqtt.CallbackAPIVersion.VERSION1, "ipmi2mqtt") #create new instance
    if hasattr(config.mqtt, 'username') and hasattr(config.mqtt, 'password'):
        mqtt.username_pw_set(config.mqtt.username, config.mqtt.password)

    mqtt.connect(config.mqtt.host, config.mqtt.port)
    #signal.signal(signal.SIGTERM, term)
    # Possible todo: enable ping function to make sure a single instance is running
    #mqtt.subscribe("ipmi2mqtt/ping")
    #mqtt.message_callback_add("ipmi2mqtt/ping", on_ping)
    if config.output:
        print("MQTT Connected")

    for device in config.devices:
        setSubscribe = f"ipmi2mqtt/{device.name}/+/set"
        mqtt.subscribe(setSubscribe)
        setHandler = mqttSetHandler(config, device)
        mqtt.message_callback_add(setSubscribe, setHandler.message)
        if config.output:
            print(f"Subscribed to {setSubscribe} for {device.name}")

    for device in config.devices:
        soft_shutdown_subscribe = f"ipmi2mqtt/{device.name}/soft_shutdown/press"
        mqtt.subscribe(soft_shutdown_subscribe)
        soft_shutdown_handler = mqttSoftShutdownHandler(config, device)
        mqtt.message_callback_add(soft_shutdown_subscribe, soft_shutdown_handler.message)
        if config.output:
            print(f"Subscribed to {soft_shutdown_subscribe} for {device.name}")

    for device in config.devices:
        power_cycle_subscribe = f"ipmi2mqtt/{device.name}/power_cycle/press"
        mqtt.subscribe(power_cycle_subscribe)
        power_cycle_handler = mqttPowerCycleHandler(config, device)
        mqtt.message_callback_add(power_cycle_subscribe, power_cycle_handler.message)
        if config.output:
            print(f"Subscribed to {power_cycle_subscribe} for {device.name}")

    for device in config.devices:
        hard_reset_subscribe = f"ipmi2mqtt/{device.name}/hard_reset/press"
        mqtt.subscribe(hard_reset_subscribe)
        hard_reset_handler = mqttHardResetHandler(config, device)
        mqtt.message_callback_add(hard_reset_subscribe, hard_reset_handler.message)
        if config.output:
            print(f"Subscribed to {hard_reset_subscribe} for {device.name}")

    #mqtt.on_message=on_msg

    return mqtt

#Future potential handler to make sure single instance is running
def on_ping(client, userdata, message):
    if message.payload.decode('utf-8') == "Ping!":
        client.publish(message.topic, "Pong!")

def on_msg(client, userdata, message):
    print(f"MSG: {message.topic} => {message.payload.decode('utf-8')}")
    print(f"    QOS: {message.qos}, Retain: {message.retain}")

def on_state(client, userdata, message):
    print(f"STATE: {message.topic} => {message.payload.decode('utf-8')}")
    print(f"    QOS: {message.qos}, Retain: {message.retain}")

def on_set(client, userdata, message, config, device):
    print(f"SET: {message.topic} => {message.payload.decode('utf-8')}")
    print(f"    QOS: {message.qos}, Retain: {message.retain}")

def getConfig(configFile="config.yaml"):
    with open(configFile, "r") as f:
        return json.loads(json.dumps(yaml.safe_load(f)), object_hook=lambda d: SimpleNamespace(**d))

if __name__ == "__main__":
    main()
