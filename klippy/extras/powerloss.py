# Powerloss save management
#
# Copyright (C) 2025  Domingos Rodrigues <domingoslamas@gmail.com>
#
# 
import os
import shlex
import subprocess
import logging

class Powerloss:
    def __init__(self, config):
        self.name = config.get_name()
        self.printer = printer = config.get_printer()
        self.gcode = printer.lookup_object('gcode')
        self.pin = config.get('pin')
        self.last_state = 0
        self.powerloss_save_file = '/home/xpim/printer_data/storage/tester.cfg'
        self.last_bed_temp = 0.0

        # Relay
        self.relay_pin = config.get('pin_relay').split('gpio')[1]
        os.system('pinctrl set '+self.relay_pin+' pu op')
        os.system('pinctrl set '+self.relay_pin+' pu dh')

        # Powerloss input
        buttons = self.printer.load_object(config, "buttons")
        buttons.register_debounce_button(self.pin, self.button_callback
                                             , config)

        self.reactor = self.printer.get_reactor()
        self.duration = 2.0
        self.timer_handler = None
        self.inside_timer = self.repeat = False
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        self.gcode.register_command(
            "PRINT_POWERLOSS_DATA",
            self.cmd_PRINT_POWERLOSS_DATA,
            desc=self.cmd_PRINT_POWERLOSS_DATA_help,
        )

        self.gcode.register_command(
            "SAVE_POWERLOSS_SHUTDOWN",
            self.cmd_SAVE_POWERLOSS_SHUTDOWN,
            desc=self.cmd_SAVE_POWERLOSS_SHUTDOWN_help,
        )

        self.gcode.register_command(
            "SAVE_VARS",
            self.cmd_SAVE_VARS,
            desc=self.cmd_SAVE_VARS_help,
        )

        self.gcode.register_command(
            "POWERLOSS_RESTORE_CONTINUE",
            self.cmd_POWERLOSS_RESTORE_CONTINUE,
            desc=self.cmd_POWERLOSS_RESTORE_CONTINUE_help,
        )

        self.gcode.register_command(
            "POWERLOSS_RESTORE_CANCEL",
            self.cmd_POWERLOSS_RESTORE_CANCEL,
            desc=self.cmd_POWERLOSS_RESTORE_CANCEL_help,
        )

        self.gcode.register_command(
            "STOP_POWERLOSS",
            self.cmd_POWERLOSS_RESTORE_CANCEL,
            desc=self.cmd_POWERLOSS_RESTORE_CANCEL_help,
        )

        self.gcode.register_command(
            "POWERLOSS_TEST_1",
            self.cmd_POWERLOSS_TEST_1,
            desc=self.cmd_POWERLOSS_TEST_help,
        )

        self.gcode.register_command(
            "POWERLOSS_TEST_0",
            self.cmd_POWERLOSS_TEST_0,
            desc=self.cmd_POWERLOSS_TEST_help,
        )

        self.gcode.register_command(
            "POWERLOSS_TEST_SFS",
            self.cmd_POWERLOSS_TEST_SFS,
            desc=self.cmd_POWERLOSS_TEST_help,
        )


    def _set_pin(self, print_time, value):
        self.relay_pin_pin.set_digital(print_time, value)

    def _handle_ready(self):
        waketime = self.reactor.NEVER
        if self.duration:
            waketime = self.reactor.monotonic() + self.duration
        self.timer_handler = self.reactor.register_timer(
            self._gcode_timer_event, waketime)

    def _gcode_timer_event(self, eventtime):
        self.inside_timer = True
        try:
            if self._powerloss_check_present():
                # Set bed temp
                if self.last_bed_temp >0:
                    try:
                        pheaters = self.printer.lookup_object('heaters')
                        bed = self.printer.lookup_object('heater_bed').heater
                        pheaters.set_temperature(bed, self.last_bed_temp, False)
                    except:
                        curr_bed_temp = float(self.printer.lookup_object('temperature_sensor bed_center').last_temp) 
                        bed_set_temp = float(self.last_bed_temp)+15
                        if bed_set_temp > 100: 
                            bed_set_temp = 100.0

                        pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_1'), bed_set_temp, False)
                        pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_2'), bed_set_temp, False)
                        pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_3'), bed_set_temp, False)
                        pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_4'), bed_set_temp, False)
 

                self.gcode.respond_raw("// action:prompt_begin Powerloss detected!")
                self.gcode.respond_raw("// action:prompt_text Check if any parts are unstuck from buildplate and remove them before continuing")
                self.gcode.respond_raw("// action:prompt_button Continue print|POWERLOSS_RESTORE_CONTINUE|Continue")
                self.gcode.respond_raw("// action:prompt_button Cancel print|POWERLOSS_RESTORE_CANCEL|Cancel")
                self.gcode.respond_raw("// action:prompt_show")
                

                logging.exception("POWERLOSS: Restore script ran")
            else:
                self.gcode.respond_raw("// No powerloss to restore.")
                
                logging.exception("POWERLOSS: No restore script ran")
        except Exception:
            logging.exception("Script running error")
        nextwake = self.reactor.NEVER
        if self.repeat:
            nextwake = eventtime + self.duration
        self.inside_timer = self.repeat = False
        return nextwake
    
    def button_callback(self, eventtime, state):
        if state:
            # Force all stepper motor enable pins off
            stepper_enable = self.printer.lookup_object('stepper_enable')
            for el in stepper_enable.enable_lines.values():
                el.motor_disable(self.printer.get_reactor().monotonic())

            # Block all gcode execution
            dispatch = self.printer.lookup_object('gcode')
            dispatch.stop_now = True

            # Force flush gcode buffer and planned motion
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.flush_step_generation()

            # Stop SD print immediately
            sd_card = self.printer.lookup_object('virtual_sdcard')
            try:
                sd_card.must_pause_work = True               
                sd_card.current_file.close()
            except:
                pass

            try:
                # Only save if printing and no current save is present!
                if (self.printer.lookup_object('print_stats').state == "printing" and not self._powerloss_check_present()):
                    self.powerloss_save()
                    self.printer.invoke_shutdown('Power Failure detected! Print saved!')
                else:
                    self.printer.invoke_shutdown('Power Failure detected! Print not saved!')
                #os.system('sudo shutdown -h now')
                os.system('pinctrl set '+self.relay_pin+' pu dl')
            except:
                logging.exception("Powerloss script error")

    def _powerloss_check_present(self):
        save_file = open(self.powerloss_save_file, "r")

        has_powerloss = False

        for line in save_file:
            if "CPWL = 1.0" in line:
                has_powerloss = True
            if "b_temp = " in line:
                self.last_bed_temp = float(line.split("b_temp = ")[1])
        save_file.close()

        return has_powerloss

    def _powerloss_delete(self):
        save_file = open(self.powerloss_save_file, "r")
        temp = save_file.readlines()
        save_file.close()

        save_file = open(self.powerloss_save_file, "w")

        for line in temp:
            if "CPWL = 1.0" in line:
                save_file.write("CPWL = 0.0\n")
            else:
                save_file.write(line)
        save_file.close()


    def powerloss_save(self,force = False,no_save = False):
        # Check if a print is running otherwise skip saving (file position of zero means no file being read)
        sd_pos           = self.printer.lookup_object('virtual_sdcard').file_position
        if sd_pos == 0 and not force:
            return

        # Check if there is already a powerloss save present, if so skip saving to avoid data loss
        if self._powerloss_check_present() and not force:
            return
    

        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()

        toolhead = self.printer.lookup_object('toolhead')
        extruder = toolhead.get_extruder()
        extruder_temp    = extruder.get_heater().target_temp

        #self.gcode.respond_raw("Extruder temp before: "+ str(extruder_temp))

        # If extruder temp is set to zero it means that timeout has occured so read the restore temp
        if(extruder_temp == 0):
            extruder_temp = dict(self.printer.lookup_object('gcode_macro RESUME').variables)["extruder_temp"]

        #self.gcode.respond_raw("Extruder temp after : "+ str(extruder_temp))

        # Handle POM meter alternate bed
        try:
            bed = self.printer.lookup_object('heater_bed').heater
            bed_temp         = bed.target_temp
        except:
            #try:
            bed = self.printer.lookup_object('heater_generic Bed_1')
            bed_temp         = bed.target_temp -15
            #except:
            #    bed_temp = 0
            
             
        filename         = self.printer.lookup_object('print_stats').filename

        # toolhead.get_position() is equal to printer.gcode_move.gcode_position
        pos_x            = toolhead.get_position()[0]
        pos_y            = toolhead.get_position()[1]
        #pos_z            = toolhead.get_position()[2]

        # attempt to read real current position for restore
        gcode_move = self.printer.lookup_object('gcode_move')
        pos_z            = gcode_move.last_position[2]
        pos_z_print      = toolhead.get_position()[2]

        try:
            mesh_z           = self.printer.lookup_object('bed_mesh').z_mesh
        except:
            mesh_z= None

        if mesh_z is None:
            output_mesh_z = 0.0
        else:
            output_mesh_z = mesh_z.calc_z(pos_x,pos_y)
        
        
        #sd_pos           = self.printer.lookup_object('virtual_sdcard').file_position
        origin_z         = self.printer.lookup_object('gcode_move').homing_position[2]
        fan_speed        = self.printer.lookup_object('fan').fan.last_fan_value
        print_speed      = self.printer.lookup_object('gcode_move').speed
        max_accel        = toolhead.max_accel
        max_speed        = toolhead.max_velocity
        max_scv          = toolhead.square_corner_velocity
        pressure_advance = extruder.extruder_stepper.pressure_advance
        pwl_save         = 1.0
        idle_status      = self.printer.lookup_object('idle_timeout').get_status(eventtime)
        print_time       = idle_status['printing_time']

        def check_filament_sensor(name):
            try:
                sensor = self.printer.lookup_object('filament_motion_sensor '+name)
                return str(sensor.runout_helper.sensor_enabled)
            except:
                return 'NA'

        SFS_T0           = check_filament_sensor('SFS_T0')
        SFS_T0_SW        = check_filament_sensor('SFS_T0_SW')
        SFS_T1           = check_filament_sensor('SFS_T1')
        SFS_T1_SW        = check_filament_sensor('SFS_T1_SW')
        SFS_T2           = check_filament_sensor('SFS_T2')
        SFS_T2_SW        = check_filament_sensor('SFS_T2_SW')
        SFS_T3           = check_filament_sensor('SFS_T3')
        SFS_T3_SW        = check_filament_sensor('SFS_T3_SW')
        SFS_T4           = check_filament_sensor('SFS_T4')
        SFS_T4_SW        = check_filament_sensor('SFS_T4_SW')

        multiply         = dict(self.printer.lookup_object('gcode_macro Disable_Multiplication').variables)["multiplication_state"]

        def write_powerloss_file(in_file):
            to_write = open(in_file, "w")

            to_write.write("[variables]\n")
            to_write.write("CPWL = "                 + str(pwl_save)           + "\n")
            to_write.write("e_temp = "               + str(extruder_temp)      + "\n")
            to_write.write("c_extruder = '"          + str(extruder.name)      + "'\n")
            to_write.write("c_speed = "              + str(print_speed)        + "\n")
            to_write.write("c_fan = "                + str(fan_speed)          + "\n")
            to_write.write("c_accel = "              + str(max_accel)          + "\n")
            to_write.write("c_velocity = "           + str(max_speed)          + "\n")
            to_write.write("c_square = "             + str(max_scv)            + "\n")
            to_write.write("c_lin = "                + str(pressure_advance)   + "\n")
            to_write.write("b_temp = "               + str(bed_temp)           + "\n")
            to_write.write("c_file = '"              + str(filename)           + "'\n")
            to_write.write("sd_bytes = "             + str(sd_pos)             + "\n")
            to_write.write("c_pos_x = "              + str(pos_x)              + "\n")
            to_write.write("c_pos_y = "              + str(pos_y)              + "\n")
            to_write.write("c_pos_z = "              + str(pos_z)              + "\n")
            to_write.write("c_pos_z_print = "        + str(pos_z_print)        + "\n")
            to_write.write("c_mesh_z = "             + str(output_mesh_z)      + "\n")
            to_write.write("c_origin_z = "           + str(origin_z)           + "\n")
            to_write.write("last_save = "            + str(print_time)         + "\n")
            to_write.write("c_sfs_t0 = "             + str(SFS_T0   )          + "\n")
            to_write.write("c_sfs_t0_sw = "          + str(SFS_T0_SW)          + "\n")
            to_write.write("c_sfs_t1 = "             + str(SFS_T1   )          + "\n")
            to_write.write("c_sfs_t1_sw = "          + str(SFS_T1_SW)          + "\n")
            to_write.write("c_sfs_t2 = "             + str(SFS_T2   )          + "\n")
            to_write.write("c_sfs_t2_sw = "          + str(SFS_T2_SW)          + "\n")
            to_write.write("c_sfs_t3 = "             + str(SFS_T3   )          + "\n")
            to_write.write("c_sfs_t3_sw = "          + str(SFS_T3_SW)          + "\n")
            to_write.write("c_sfs_t4 = "             + str(SFS_T4   )          + "\n")
            to_write.write("c_sfs_t4_sw = "          + str(SFS_T4_SW)          + "\n")
            to_write.write("multiply = "             + str(multiply)           + "\n")
            to_write.write("\n") # not sure if necessary but normal storage file adds this padding on the end of file
            to_write.close()

        if(not no_save):
            # create powerloss variable file from received data
            write_powerloss_file(self.powerloss_save_file)

            # force OS sync to make sure file cache is flushed to disk
            os.system('sync')
        else:
            self.gcode.respond_raw("Extruder temp  : "+str(extruder_temp))
            self.gcode.respond_raw("Bed temp       : "+str(bed_temp))
            self.gcode.respond_raw("Active extruder: "+str(extruder.name))
            self.gcode.respond_raw("Filename       : "+str(filename))
            self.gcode.respond_raw("Position X     : "+str(pos_x))
            self.gcode.respond_raw("Position Y     : "+str(pos_y))
            self.gcode.respond_raw("Position Z     : "+str(pos_z))
            self.gcode.respond_raw("Mesh Z         : "+str(output_mesh_z))

            self.gcode.respond_raw("Sd position    : "+str(sd_pos))
            self.gcode.respond_raw("Z Origin       : "+str(origin_z))
            self.gcode.respond_raw("Print speed    : "+str(print_speed))
            self.gcode.respond_raw("Fan speed      : "+str(fan_speed))
            self.gcode.respond_raw("Max accel      : "+str(max_accel))
            self.gcode.respond_raw("Max speed      : "+str(max_speed))
            self.gcode.respond_raw("Max SCV        : "+str(max_scv))
            self.gcode.respond_raw("Pressure Adv.  : "+str(pressure_advance))
            self.gcode.respond_raw("PWL            : "+str(pwl_save))
            self.gcode.respond_raw("Print time     : "+str(print_time))

            self.gcode.respond_raw("SFS_T0         : "+str(SFS_T0))
            self.gcode.respond_raw("SFS_T0_SW      : "+str(SFS_T0_SW))
            self.gcode.respond_raw("SFS_T1         : "+str(SFS_T1))
            self.gcode.respond_raw("SFS_T1_SW      : "+str(SFS_T1_SW))
            self.gcode.respond_raw("SFS_T2         : "+str(SFS_T2))
            self.gcode.respond_raw("SFS_T2_SW      : "+str(SFS_T2_SW))
            self.gcode.respond_raw("SFS_T3         : "+str(SFS_T3))
            self.gcode.respond_raw("SFS_T3_SW      : "+str(SFS_T3_SW))
            self.gcode.respond_raw("SFS_T4         : "+str(SFS_T4))
            self.gcode.respond_raw("SFS_T4_SW      : "+str(SFS_T4_SW))
            self.gcode.respond_raw("Multiply       : "+str(multiply))

    cmd_SAVE_POWERLOSS_SHUTDOWN_help = "Save all powerloss data and shutdown machine"
    def cmd_SAVE_POWERLOSS_SHUTDOWN(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.lookahead.reset()

        dispatch = self.printer.lookup_object('gcode')
        dispatch.gcode_handlers = dispatch.base_gcode_handlers
        dispatch._build_status_commands()

        self.powerloss_save()

        # force OS sync to make sure file cache is flushed to disk
        os.system('sudo shutdown -h now')

    cmd_SAVE_VARS_help = "Save Powerloss data"
    def cmd_SAVE_VARS(self,gcmd):
        self.powerloss_save(True)

    cmd_PRINT_POWERLOSS_DATA_help = "Print all powerloss data to save, without saving"
    def cmd_PRINT_POWERLOSS_DATA(self, gcmd):
        self.powerloss_save(True,True)

    cmd_POWERLOSS_RESTORE_CONTINUE_help = "Execute Powerloss recovery"
    def cmd_POWERLOSS_RESTORE_CONTINUE(self, gcmd):
        self.gcode.respond_raw("// action:prompt_end")
        self.gcode.respond_raw("// Restoring Powerloss")

        # Read all data from storage file
        pwl_save        = "NA"
        extruder_temp   = "NA"
        c_extruder      = "NA"
        c_speed         = "NA"
        fan_speed       = "NA"
        max_accel       = "NA"
        max_speed       = "NA"
        max_scv         = "NA"
        pressure_advance= "NA"
        bed_temp        = "NA"
        filename        = "NA"
        sd_pos          = "NA"
        pos_x           = "NA"
        pos_y           = "NA"
        pos_z           = "NA"
        pos_z_print     = "NA"
        output_mesh_z   = "NA"
        origin_z        = "NA"
        print_time      = "NA"
        SFS_T0          = "NA"
        SFS_T0_SW       = "NA"
        SFS_T1          = "NA"
        SFS_T1_SW       = "NA"
        SFS_T2          = "NA"
        SFS_T2_SW       = "NA"
        SFS_T3          = "NA"
        SFS_T3_SW       = "NA"
        SFS_T4          = "NA"
        SFS_T4_SW       = "NA"
        multiply        = "NA"

        save_file = open(self.powerloss_save_file, "r")

        def check_extract(line,name,variable):
            if (name+" ") in line:
                return line.split(name+" = ")[1].split("\n")[0]
            else:
                return variable

        for line in save_file:
            pwl_save            =check_extract(line,"CPWL"         ,pwl_save)
            extruder_temp       =check_extract(line,"e_temp"       ,extruder_temp)
            c_extruder          =check_extract(line,"c_extruder"   ,c_extruder)
            c_speed             =check_extract(line,"c_speed"      ,c_speed)
            fan_speed           =check_extract(line,"c_fan"        ,fan_speed)
            max_accel           =check_extract(line,"c_accel"      ,max_accel)
            max_speed           =check_extract(line,"c_velocity"   ,max_speed)
            max_scv             =check_extract(line,"c_square"     ,max_scv)
            pressure_advance    =check_extract(line,"c_lin"        ,pressure_advance)
            bed_temp            =check_extract(line,"b_temp"       ,bed_temp)
            filename            =check_extract(line,"c_file"       ,filename)
            sd_pos              =check_extract(line,"sd_bytes"     ,sd_pos)
            pos_x               =check_extract(line,"c_pos_x"      ,pos_x)
            pos_y               =check_extract(line,"c_pos_y"      ,pos_y)
            pos_z               =check_extract(line,"c_pos_z"      ,pos_z)
            pos_z_print         =check_extract(line,"c_pos_z_print",pos_z)
            output_mesh_z       =check_extract(line,"c_mesh_z"     ,output_mesh_z)
            origin_z            =check_extract(line,"c_origin_z"   ,origin_z)
            print_time          =check_extract(line,"last_save"    ,print_time)
            SFS_T0              =check_extract(line,"c_sfs_t0"     ,SFS_T0)
            SFS_T0_SW           =check_extract(line,"c_sfs_t0_sw"  ,SFS_T0_SW)
            SFS_T1              =check_extract(line,"c_sfs_t1"     ,SFS_T1)
            SFS_T1_SW           =check_extract(line,"c_sfs_t1_sw"  ,SFS_T1_SW)
            SFS_T2              =check_extract(line,"c_sfs_t2"     ,SFS_T2)
            SFS_T2_SW           =check_extract(line,"c_sfs_t2_sw"  ,SFS_T2_SW)
            SFS_T3              =check_extract(line,"c_sfs_t3"     ,SFS_T3)
            SFS_T3_SW           =check_extract(line,"c_sfs_t3_sw"  ,SFS_T3_SW)
            SFS_T4              =check_extract(line,"c_sfs_t4"     ,SFS_T4) 
            SFS_T4_SW           =check_extract(line,"c_sfs_t4_sw"  ,SFS_T4_SW)
            multiply            =check_extract(line,"multiply"     ,multiply)
        save_file.close()

        gcmd.respond_raw("Extruder temp  : "+str(extruder_temp))
        gcmd.respond_raw("Bed temp       : "+str(bed_temp))
        gcmd.respond_raw("Active extruder: "+str(c_extruder))
        gcmd.respond_raw("Filename       : "+str(filename))
        gcmd.respond_raw("Position X     : "+str(pos_x))
        gcmd.respond_raw("Position Y     : "+str(pos_y))
        gcmd.respond_raw("Position Z     : "+str(pos_z))
        gcmd.respond_raw("Position Z p.  : "+str(pos_z_print))
        gcmd.respond_raw("Mesh Z         : "+str(output_mesh_z))
        gcmd.respond_raw("Z origin       : "+str(origin_z))
        gcmd.respond_raw("Sd position    : "+str(sd_pos))
        gcmd.respond_raw("Print speed    : "+str(c_speed))
        gcmd.respond_raw("Fan speed      : "+str(fan_speed))
        gcmd.respond_raw("Max accel      : "+str(max_accel))
        gcmd.respond_raw("Max speed      : "+str(max_speed))
        gcmd.respond_raw("Max SCV        : "+str(max_scv))
        gcmd.respond_raw("Pressure Adv.  : "+str(pressure_advance))
        gcmd.respond_raw("PWL            : "+str(pwl_save))
        gcmd.respond_raw("Print time     : "+str(print_time))
        gcmd.respond_raw("SFS_T0         : "+str(SFS_T0))
        gcmd.respond_raw("SFS_T0_SW      : "+str(SFS_T0_SW))
        gcmd.respond_raw("SFS_T1         : "+str(SFS_T1))
        gcmd.respond_raw("SFS_T1_SW      : "+str(SFS_T1_SW))
        gcmd.respond_raw("SFS_T2         : "+str(SFS_T2))
        gcmd.respond_raw("SFS_T2_SW      : "+str(SFS_T2_SW))
        gcmd.respond_raw("SFS_T3         : "+str(SFS_T3))
        gcmd.respond_raw("SFS_T3_SW      : "+str(SFS_T3_SW))
        gcmd.respond_raw("SFS_T4         : "+str(SFS_T4))
        gcmd.respond_raw("SFS_T4_SW      : "+str(SFS_T4_SW))
        gcmd.respond_raw("Multiply state : "+str(multiply))

        # AUX functions
        def wait_for_temp(function,name,temperature):
            self.gcode.respond_raw("Waiting for "+ name +" temperature to reach >="+str(round(temperature,1))+"ºC")
            reactor = self.printer.get_reactor()
            current_temp = 0
            while(current_temp < temperature-1):
                current_temp = function(reactor.monotonic()) [0]
                reactor.pause(self.reactor.monotonic() + 0.1)
        
        def wait_for_extruder_temp(temperature,heater,name = ""):
            # Reset heater temp in case something failed
            pheaters.set_temperature(heater, float(temperature), False)
            # Wait
            wait_for_temp(heater.get_temp,"extruder"+name,temperature)
        
        def wait_for_bed_temp(temperature):
            try:
                wait_for_temp(self.printer.lookup_object('heater_bed').heater.get_temp,"bed heater",temperature)
            except:
                # POM meter stabilization method
                bed_temp = self.printer.lookup_object('temperature_sensor bed_center').last_temp 
                if bed_temp < temperature:

                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_1'), 100, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_2'), 100, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_3'), 100, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_4'), 100, False)

                    while(bed_temp < float(temperature)-10):
                        bed_temp = self.printer.lookup_object('temperature_sensor bed_center').last_temp 
                        reactor.pause(self.reactor.monotonic() + 0.1)

                    bed_set_temp = float(temperature)+15
                    if bed_set_temp > 100: 
                        bed_set_temp = 100.0

                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_1'), bed_set_temp, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_2'), bed_set_temp, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_3'), bed_set_temp, False)
                    pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_4'), bed_set_temp, False)

                    wait_for_temp(self.printer.lookup_object('heater_generic Bed_1').get_temp,"bed heater 1",temperature)
                    wait_for_temp(self.printer.lookup_object('heater_generic Bed_2').get_temp,"bed heater 2",temperature)
                    wait_for_temp(self.printer.lookup_object('heater_generic Bed_3').get_temp,"bed heater 3",temperature)
                    wait_for_temp(self.printer.lookup_object('heater_generic Bed_4').get_temp,"bed heater 4",temperature)

        def move_to_postion(x,y,z,speed,relative=False):
            gcode_move = self.printer.lookup_object('gcode_move')
            if relative:
                gcode_move.absolute_coord= False
                gcode_move.last_position[0] += x
                gcode_move.last_position[1] += y
                gcode_move.last_position[2] += z

            else: 
                gcode_move.absolute_coord= True
                gcode_move.last_position[0] = x + gcode_move.base_position[0]
                gcode_move.last_position[1] = y + gcode_move.base_position[1]
                gcode_move.last_position[2] = z + gcode_move.base_position[2]
            
            gcode_move.move_with_transform(gcode_move.last_position, speed)

            toolhead = self.printer.lookup_object('toolhead')
            toolhead.wait_moves()
            #reactor = self.printer.get_reactor()
            #reactor.pause(self.reactor.monotonic() + 2)

        def run_gcode(gcode):
            self.gcode.respond_raw("Running Gcode command: "+gcode.split()[0] + " with commandline: "+ gcode)
            command = self.gcode.create_gcode_command(gcode.split()[0], gcode, {})
            handler = self.gcode.gcode_handlers.get(gcode.split()[0], None)
            handler(gcmd)

        def restore_filament_sensor(name,state):
            try:
                sensor = self.printer.lookup_object('filament_motion_sensor '+name, None)
                sensor.runout_helper.sensor_enabled = int(state)
            except:
                pass


        gcode_move = self.printer.lookup_object('gcode_move')

        # Set bed temp
        pheaters = self.printer.lookup_object('heaters')

        # handle POM meter 4 bed setup
        if float(bed_temp) > 0:
            try:
                bed_heater = self.printer.lookup_object('heater_bed').heater
                pheaters.set_temperature(bed_heater, float(bed_temp), False)
            except:

                curr_bed_temp = float(self.printer.lookup_object('temperature_sensor bed_center').last_temp) 
                bed_set_temp = float(bed_temp)+15
                if bed_set_temp > 100: 
                    bed_set_temp = 100.0

                pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_1'), bed_set_temp, False)
                pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_2'), bed_set_temp, False)
                pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_3'), bed_set_temp, False)
                pheaters.set_temperature(self.printer.lookup_object('heater_generic Bed_4'), bed_set_temp, False)
 
        # Reinstate multiply state
        v = dict(self.printer.lookup_object('gcode_macro Disable_Multiplication').variables)
        v["multiplication_state"] = int(multiply)
        self.printer.lookup_object('gcode_macro Disable_Multiplication').variables = v
        
        # Set extruder temp to 160ºC
        extruder_heater = self.printer.lookup_object('toolhead').get_extruder().get_heater()
        pheaters.set_temperature(extruder_heater, 160.0, False)

        # Set temps for the other extruder if multiply is on
        if int(multiply) >= 1:
            extruder1_heater = self.printer.lookup_object('extruder1').get_heater()
            pheaters.set_temperature(extruder1_heater, 160, False)
        if int(multiply) >= 2:
            extruder2_heater = self.printer.lookup_object('extruder2').get_heater()
            pheaters.set_temperature(extruder2_heater, 160, False)
        if int(multiply) >= 3:
            extruder3_heater = self.printer.lookup_object('extruder3').get_heater()
            pheaters.set_temperature(extruder3_heater, 160, False)
        if int(multiply) >= 4:
            extruder4_heater = self.printer.lookup_object('extruder4').get_heater()
            pheaters.set_temperature(extruder4_heater, 160, False)

        # Set fan speed
        value = float(fan_speed) / 255.
        self.printer.lookup_object('fan').fan.set_speed_from_command(value)

        # Activate extruder
        current_extruder = self.printer.lookup_object(c_extruder.strip("'"))
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        toolhead.set_extruder(current_extruder, 0)
        self.printer.send_event("extruder:activate_extruder")

        # Wait for extruder to reach above 160ºC
        wait_for_extruder_temp(160.0,extruder_heater)

        if int(multiply) >= 1:
            wait_for_extruder_temp(160.0,extruder1_heater," 1")
        if int(multiply) >= 2:
            wait_for_extruder_temp(160.0,extruder2_heater," 2")
        if int(multiply) >= 3:
            wait_for_extruder_temp(160.0,extruder3_heater," 3")
        if int(multiply) >= 4:
            wait_for_extruder_temp(160.0,extruder4_heater," 4")

        # Wait two seconds and re-check to avoid false positives caused by 
        reactor = self.printer.get_reactor()
        reactor.pause(self.reactor.monotonic() + 2)
        wait_for_extruder_temp(160.0,extruder_heater)

        # Set Z position
        toolhead.set_position([float(pos_x), float(pos_y), float(pos_z)], homing_axes="xyz")
        gcode_move.last_position[0] = float(pos_x)
        gcode_move.last_position[1] = float(pos_y)
        gcode_move.last_position[2] = float(pos_z)

        # Move Z away 5mm
        move_to_postion(0,0,5,3000,True)

        # Revert mesh correction if exists
        # Disabled, appear to be overcorrecting!
        #if output_mesh_z != 'NA':
        #    gcode_move.last_position[2] += float(output_mesh_z)
        #    gcode_move.move_with_transform(gcode_move.last_position, 3000)
        #    gcode_move.absolute_coord= True

        # Re-set Z position to correct (previous + 5mm)
        toolhead.set_position([float(pos_x), float(pos_y), float(pos_z)+5+float(output_mesh_z)], homing_axes="xyz")
        gcode_move.last_position[0] = float(pos_x)
        gcode_move.last_position[1] = float(pos_y)
        gcode_move.last_position[2] = float(pos_z)+5+float(output_mesh_z)

        # Force loading bed mesh
        try: 
            self.printer.lookup_object('bed_mesh').pmgr.load_profile("current_print_mesh")
        except:
            self.gcode.respond_raw("Required mesh is missing Powerloss will attempt continue without it!")

        # Set Z offset
        if origin_z != 'NA':
            gcode_move.homing_position[2] = float(origin_z)
            gcode_move.base_position[2] += float(origin_z)
        else:
            self.gcode.respond_raw("No Z offset registered will continue without it!")

        # Set velocity accel and scv
        #toolhead.set_max_velocities(float(max_speed),float(max_accel),float(max_scv),0.5)
        toolhead.max_velocity = float(max_speed)
        toolhead.max_accel = float(max_accel)
        toolhead.square_corner_velocity = float(max_scv)
        toolhead.min_cruise_ratio = 0.5
        toolhead._calc_junction_deviation()

        # SET PA
        toolhead.get_extruder().extruder_stepper._set_pressure_advance(float(pressure_advance),0.04)
        
        # Set relative extrusion
        gcode_move.absolute_extrude = False

        # G28 X Y
        homing = self.printer.lookup_object('homing')
        #command = self.gcode.create_gcode_command("G28 X Y", "G28 X Y", {})
        #homing.cmd_G28(command)
        homing.HOMEXY()

        # Set extruder temp
        extruder_heater = self.printer.lookup_object('toolhead').get_extruder().get_heater()
        pheaters.set_temperature(extruder_heater, float(extruder_temp), False)

        # Set temps for the other extruder if multiply is on
        if int(multiply) >= 1:
            extruder1 = self.printer.lookup_object('extruder1')
            pheaters.set_temperature(extruder1.get_heater(), float(extruder_temp), False)
        if int(multiply) >= 2:
            extruder2 = self.printer.lookup_object('extruder2')
            pheaters.set_temperature(extruder2.get_heater(), float(extruder_temp), False)
        if int(multiply) >= 3:
            extruder3 = self.printer.lookup_object('extruder3')
            pheaters.set_temperature(extruder3.get_heater(), float(extruder_temp), False)
        if int(multiply) >= 4:
            extruder4 = self.printer.lookup_object('extruder4')
            pheaters.set_temperature(extruder4.get_heater(), float(extruder_temp), False)

        # Wait for print temp
        wait_for_extruder_temp(float(extruder_temp),extruder_heater,"")
        if int(multiply) >= 1:
            wait_for_extruder_temp(float(extruder_temp),extruder1_heater," 1")
        if int(multiply) >= 2:
            wait_for_extruder_temp(float(extruder_temp),extruder2_heater," 2")
        if int(multiply) >= 3:
            wait_for_extruder_temp(float(extruder_temp),extruder3_heater," 3")
        if int(multiply) >= 4:
            wait_for_extruder_temp(float(extruder_temp),extruder4_heater," 4")
        if float(bed_temp) > 0:
            wait_for_bed_temp(float(bed_temp))

        ## Restore filament sensors status
        restore_filament_sensor("SFS_T0"   ,SFS_T0)
        restore_filament_sensor("SFS_T0_SW",SFS_T0_SW)
        restore_filament_sensor("SFS_T1"   ,SFS_T1)
        restore_filament_sensor("SFS_T1_SW",SFS_T1_SW)
        restore_filament_sensor("SFS_T2"   ,SFS_T2)
        restore_filament_sensor("SFS_T2_SW",SFS_T2_SW)
        restore_filament_sensor("SFS_T3"   ,SFS_T3)
        restore_filament_sensor("SFS_T3_SW",SFS_T3_SW)
        restore_filament_sensor("SFS_T4"   ,SFS_T4)
        restore_filament_sensor("SFS_T4_SW",SFS_T4_SW)

        # Enable extruder sync according to multiply
        if int(multiply) >= 1:
            self.printer.lookup_object('extruder1').extruder_stepper.sync_to_extruder("extruder")
            self.printer.lookup_object('extruder_stepper e2_aux').extruder_stepper.sync_to_extruder("extruder")
        if int(multiply) >= 2:
            self.printer.lookup_object('extruder2').extruder_stepper.sync_to_extruder("extruder")
            self.printer.lookup_object('extruder_stepper e3_aux').extruder_stepper.sync_to_extruder("extruder")
        if int(multiply) >= 3:
            self.printer.lookup_object('extruder3').extruder_stepper.sync_to_extruder("extruder")
            self.printer.lookup_object('extruder_stepper e4_aux').extruder_stepper.sync_to_extruder("extruder")
        if int(multiply) >= 4:
            self.printer.lookup_object('extruder4').extruder_stepper.sync_to_extruder("extruder")
            self.printer.lookup_object('extruder_stepper e5_aux').extruder_stepper.sync_to_extruder("extruder")
        

        # TODO: Check if Z offset should be compensated here??
        #SET_GCODE_OFFSET Z={printer.save_variables.variables.g_offset} MOVE=0

        #G1 X{printer.save_variables.variables.c_pos_x} Y{printer.save_variables.variables.c_pos_y} F8000
        move_to_postion(float(pos_x),float(pos_y),float(pos_z_print)+5,150)
        
        #G1 Z{printer.save_variables.variables.c_pos_z}
        move_to_postion(float(pos_x),float(pos_y),float(pos_z_print),150)

        # Reset Gcode print-speed
        self.printer.lookup_object('gcode_move').speed = float(50)

        # M23 Filename
        sd_card = self.printer.lookup_object('virtual_sdcard')
        sd_card._load_file(self.gcode,str(filename).strip("'"))

        # M26 position
        sd_card.file_position = int(sd_pos)

        # M24
        sd_card.do_resume()

        # Delete powerloss file 
        self._powerloss_delete()


    cmd_POWERLOSS_RESTORE_CANCEL_help = "Cancel powerloss recovery and delete powerloss data"
    def cmd_POWERLOSS_RESTORE_CANCEL(self, gcmd):
        self.gcode.respond_raw("// action:prompt_end")
        #disable bed
        pheaters = self.printer.lookup_object('heaters')
        try:
            bed_heater = self.printer.lookup_object('heater_bed').heater
            pheaters.set_temperature(bed_heater, 0, False)
        except:
            bed_heater_1 = self.printer.lookup_object('heater_generic Bed_1')
            bed_heater_2 = self.printer.lookup_object('heater_generic Bed_2')
            bed_heater_3 = self.printer.lookup_object('heater_generic Bed_3')
            bed_heater_4 = self.printer.lookup_object('heater_generic Bed_4')
            pheaters.set_temperature(bed_heater_1, 0, False)
            pheaters.set_temperature(bed_heater_2, 0, False)
            pheaters.set_temperature(bed_heater_3, 0, False)
            pheaters.set_temperature(bed_heater_4, 0, False)
        self.gcode.respond_raw("Powerloss restore canceled. Use POWERLOSS_RESTORE_CONTINUE to force a restore using the saved data.")
        self._powerloss_delete()

    cmd_POWERLOSS_TEST_help = "Test function"

    def cmd_POWERLOSS_TEST_1(self, gcmd):
        os.system('pinctrl set '+self.relay_pin+' pu dh')

    def cmd_POWERLOSS_TEST_0(self, gcmd):
        os.system('pinctrl set '+self.relay_pin+' pu dl')

    def cmd_POWERLOSS_TEST_SFS(self, gcmd):
        def check_filament_sensor(name):
            try:
                self.gcode.respond_raw('filament_motion_sensor '+name)
                sensor = self.printer.lookup_object('filament_motion_sensor '+name)
                self.gcode.respond_raw('FOUND')
                self.gcode.respond_raw(str(sensor.get_status(self.reactor.monotonic())))
                runout_helper = sensor.runout_helper
                self.gcode.respond_raw('FOUND Runout helper')
                self.gcode.respond_raw(str(sensor.runout_helper.sensor_enabled))
                self.gcode.respond_raw('READ')
            except:
                self.gcode.respond_raw('FAILED')
                

        check_filament_sensor('SFS_T0')
        check_filament_sensor('SFS_T0_SW')
        check_filament_sensor('SFS_T1')
        check_filament_sensor('SFS_T1_SW')
        check_filament_sensor('SFS_T2')
        check_filament_sensor('SFS_T2_SW')
        check_filament_sensor('SFS_T3')
        check_filament_sensor('SFS_T3_SW')
        check_filament_sensor('SFS_T4')
        check_filament_sensor('SFS_T4_SW')

def load_config(config):
    return Powerloss(config)
