# --------------------------------------------------------------------------------------------------
#
#   FiniteStateMachine.py                                                 
#
#     PROJECT:    Johnny Blade
#
#   
#     Copyright (c) 2017, Adam Casey.
#     All rights reserved.
#
#     Redistribution and use in source and binary forms, with or without modification, are permitted 
#     provided that the following conditions are met:
#
#         * Redistributions of source code must retain the above copyright notice, this list of 
#           conditions and the following disclaimer.
#         * Redistributions in binary form must reproduce the above copyright notice, this list of 
#           conditions and the following disclaimer in the documentation and/or other materials provided 
#           with the distribution.
#         * Neither the name of Adam Casey nor the names of its contributors may be used to endorse or 
#           promote products derived from this software without specific prior written permission.
#
#     THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR 
#     IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND 
#     FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR 
#     CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL 
#     DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, 
#     DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, 
#     WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY 
#     WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#---------------------------------------------------------------------------------------------------------

#---------------------------------------------------------------------------------------------------------
# Dependencies
#---------------------------------------------------------------------------------------------------------

from traceback import print_exception, format_tb, print_exc
from sys import exc_info
from json import dumps
from time import sleep

from Queue import PriorityQueue, Empty, Full
from threading import Thread, Event as ThreadEvent

from States import AbstractState, Message

#---------------------------------------------------------------------------------------------------------
# Setup
#---------------------------------------------------------------------------------------------------------

#---------------------------------------------------------------------------------------------------------
# CLASS: FiniteStateMachine
#
# Describes the finite state machine, and all states and events.
#---------------------------------------------------------------------------------------------------------

class FiniteStateMachine(object):
    """
    Describes the finite state machine, and all states and events.  The FSM runs in a separate thread that
    never exits
    """
    def __init__(self):

        # Set the current state to the startup state
        self.states = {}

        # Populate the states
        self.registerStates()

      
        # Set up the priority message queue
        self.messageQueue = PriorityQueue(0)

        # Set the viewmodel
        self.systemViewModel = SystemStatusViewModel()

        self.stopEvent = ThreadEvent()

        # Start the thread
        self.fsmThread = Thread(target=self._finiteStateMachineThreadFunction)
        self.fsmThread.start()

    def registerStates(self):
        states = AbstractState.getAllStates()
        for state in states:
            if state.identifier in self.states:
                raise Exception, "State %s already configured"%(state.identifier)
            else:
                self.states[state.identifier] = state


    #---------------------------------------------------------------------------------------------------------
    # METHOD: initializeDatabase
    #---------------------------------------------------------------------------------------------------------

    def initializeDatabase(self):
        """
        Perform database initialization
        """
        return self.currentState.name


    #---------------------------------------------------------------------------------------------------------
    # METHOD: currentStateName
    #---------------------------------------------------------------------------------------------------------

    def currentStateName(self):
        """
        Get the current state name
        """
        return self.currentState.name

    #---------------------------------------------------------------------------------------------------------
    # METHOD: postMessage
    #---------------------------------------------------------------------------------------------------------

    def postMessage(self, message, priority):
        """
        Post a message with the indicated priority to the message queue
        """
        qsize=0
        try:
            self.messageQueue.put((priority, message), False, 0)
            qsize = self.messageQueue.qsize()
        except Full:
            logger.error("FSM: Message Queue is Full: qsize: %d", qsize)
            return
        
        msg = "FSM: postMessage: 0x%X, qsize: %d" % (message.identifier, qsize)
        logger.info(msg)

    #---------------------------------------------------------------------------------------------------------
    # METHOD: postMessage
    #---------------------------------------------------------------------------------------------------------

    def clearMessageQueue(self):
        """
        Clears the message queue.  Note that this is the only way to do this using the documented
        public interface.  There is an undocumented method called clear() but lets stick to the 
        public commands
        """
        self.messageQueue = PriorityQueue(0)


    #---------------------------------------------------------------------------------------------------------
    # METHOD: sendToBrowser
    #---------------------------------------------------------------------------------------------------------

    def sendToBrowser(self, event, jsonstring):
        """
        Send a message over socket.io to the browser
        """
        logger.info("FSM: sendToBrowser: %s: %s", event, jsonstring)
        self.socketio.emit(event, jsonstring)

    #---------------------------------------------------------------------------------------------------------
    # METHOD: setSerialApplicationLayer
    #---------------------------------------------------------------------------------------------------------

    def setSerialApplicationLayer(self, a):
        """
        Give the FSM a a reference to the instance of SerialApplicationLayer that uses it as mainFSM. This is
        done because the state functions need access to the methods of the SerialApplicationLayer and they
        only have a reference to the FSM.

        Also - start log message polling once the serial layer is set (active).
        """
        self.serialApp = a

    #---------------------------------------------------------------------------------------------------------
    # METHOD: setTimer
    #---------------------------------------------------------------------------------------------------------

    def setTimer(self, identifier, interval=60):
        """
        Set (and start) a timer to span multiple states. Defaults to one minute.
        """
        if self.timers.get(identifier, None):
            logger.info("FiniteStateMachine.setTimer: Aborting timer with ID %s to start a new timer.", hex(identifier))
            self.timers[identifier].stop()

        timerComplete = lambda: self.postMessage(Message(Message.TIMER_COMPLETE, {'timer': identifier}), Message.Priority.MEDIUM)

        self.timers[identifier] = Timer(identifier, interval, timerComplete)
        self.timers[identifier].start()

    def setTick(self, interval=60):
        timerTick = lambda: self.postMessage(Message(Message.STATE_TICK, {}), Message.Priority.MEDIUM)

        self.timers[RepeatTimer.STATE_TICK] = RepeatTimer(RepeatTimer.STATE_TICK, interval, timerTick)

    def stopTimer(self, identifier):
        if self.timers.get(identifier, None):
            self.timers[identifier].stop()

    def stopTick(self):
        self.stopTimer(RepeatTimer.STATE_TICK)

    def stop(self):

        logger.info("Stopping FSM")
        
        # Stop all timers
        for identifier, timer in self.timers.items():
            if timer is not None:
                timer.stop()

        if self.wifi is not None:
            self.wifi.stopMonitoring()

        if self.cycleTimer is not None:
            self.cycleTimer.stop()

        # Stop serial comms
        if self.serialApp:
            self.serialApp.stop()

        # Stop FSM
        self.stopEvent.set()

        logger.info("Persisting models to disk...")
        # for modelName, model in self.models.iteritems():
        #     model.save()





    #---------------------------------------------------------------------------------------------------------
    # METHOD: _finiteStateMachineThreadFunction
    #---------------------------------------------------------------------------------------------------------

    def _finiteStateMachineThreadFunction(self):
        """
        This method runs the main finite state machine application layer.
        1. Pend for an message in the queue
        2. If the message is a state change message, then:
            a. Run the exit function of the current state
            b. Run the init function of the new state
        3. Otherwise pass the message to the message pump function of the current state
        """

        # Wait until Flask server started
        self.socketio.startEvent.wait()

        isSetToStop = self.stopEvent.isSet

        SLEEP_SECONDS_AFTER_EXCEPTION = 1

        def handle_exception(headerStr, exc):
            try:
                exc_type, exc_value, exc_traceback = exc_info()

                logger.error(headerStr)
                lines = format_exception(exc_type, exc_value, exc_traceback)
                for line in lines:
                    logger.error("%s", line)
                #print_exception(exc_type, exc_value, exc_traceback)
                tracebackStr = '\n'.join(s.strip() for s in format_tb(exc_traceback))

                # Log error to database
                description = dumps({
                    'source': 'RPI',
                    'data': {
                        'type': exc.__class__.__name__,
                        'message': exc.message,
                        'traceback': tracebackStr
                    }
                })
                LocalDB.storeEvent(self, Event_Error, description=description)
            except Exception as e:
                logger.error("UNEXPECTED EXCEPTION IN handle_exception: %s", e)

        def call_with_exception_logging(f, excHeaderStr, *args, **kwargs):
            try:
                f(*args, **kwargs)
            except Exception as e:
                handle_exception(excHeaderStr, e)
                return False
            else:
                return True

        def call_with_exception_restart(f, excHeaderStr, *args, **kwargs):
            while not isSetToStop():
                if not call_with_exception_logging(f, excHeaderStr, *args, **kwargs):
                    sleep(SLEEP_SECONDS_AFTER_EXCEPTION)
                    logger.info("Retrying...")
                else:
                    break

        def log_state_transition(typeStr, state):
            try:
                description = dumps({
                    'source': 'FSM',
                    'data': {
                        'type': typeStr,
                        'state': self.currentState
                    }
                })
                LocalDB.storeEvent(self, Event_Error, description=description)

            # Fail silently
            except:
                pass  

        def transition_states(stateFrom, stateTo):
            logger.info("FSM: transition_states: %s --> %s", stateFrom.name if stateFrom else 'null', stateTo.name)
            enteringStates = []
            exitingStates = []

            if stateFrom:

                # Find common parent
                parent = None
                stateFromHierarchy = stateFrom.stateHierarchy
                stateToHierarchy = stateTo.stateHierarchy

                for stateFromRelative in stateFromHierarchy:
                    if stateFromRelative in stateToHierarchy:
                        parent = stateFromRelative
                        break
                assert parent, "States %s and %s which should be related are not related."%(stateFrom.name, stateTo.name)

                # Exit stateFrom states up to but not including parent...
                indexOfParent = stateFromHierarchy.index(parent)
                if indexOfParent != 0:
                    exitingStates = stateFromHierarchy[0:indexOfParent]

                # Get stateTo states up to but not including parent... in reverse order (enter from parents first)
                enteringStates = stateToHierarchy[stateToHierarchy.index(parent)-1::-1]
                assert len(enteringStates) > 0, "State %s must at least enter itself"%(stateTo.name)

                for state in exitingStates:
                    call_with_exception_restart(state.exitState, "FSM Exit Exception", self)

                # Update the state variable,
                log_state_transition('State Exit', self.currentState)

                self.currentState = stateTo

            # No previous state (starting condition)
            else:
                enteringStates = reversed(stateTo.stateHierarchy)

            logger.info("FSM: NEW STATE: %s", self.currentState.name)
            
            for state in enteringStates:
                call_with_exception_restart(state.initializeState, "FSM Init Exception", self, stateFrom)
                log_state_transition('State Init', self.currentState)

        # Run the init function for the first state
        call_with_exception_restart(transition_states, "FSM initial state transition failure", None, self.currentState)

        while not isSetToStop():
            # Wait for the message queue to get a message
            qsize=0
            try:
                priority, message = self.messageQueue.get(block=True, timeout=1)
                qsize = self.messageQueue.qsize()
            except Empty:
                # No message, resume loop and try again
                continue
            
            logger.info("FSM: messageQueue.get: 0x%X, qsize: %d", message.identifier, qsize)

            # Message has arrived.  Check to see if the message is the bump state message
            if message.identifier == Message.CHANGE_STATE:

                # Clear the message queue.  This was tried and it 
                # didn't seem to work well.  Keeping around as a potential
                # future fix
                # self.clearMessageQueue()

                call_with_exception_restart(transition_states, "FSM state transition failure", self.currentState, self.states[message.parameters['NewState']])

            # Otherwise send to the current state's message pump
            else:
                call_with_exception_logging(self.currentState.processMessage, "FSM MessagePump Exception", self, message)

        logger.info("Exiting FSM...")
