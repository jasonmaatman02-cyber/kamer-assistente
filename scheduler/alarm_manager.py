from scheduler.alarm import AlarmScheduler

# Één gedeeld alarm-object voor heel je app
alarm = AlarmScheduler(callback=None)

def set_alarm(time_str, callback):
    alarm.callback = callback
    return alarm.set_alarm(time_str)

def check_alarm():
    return alarm.check_alarm()

def cancel_alarm():
    alarm.cancel_alarm()
