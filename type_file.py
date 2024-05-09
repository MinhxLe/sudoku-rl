from enum import Enum 

class Types(int, Enum): # these are categorical one-hot encoded. 
	CURSOR = 1
	POSITION = 2 # value is the axis
	REWARD = 3 # bare value
	BOX = 4
	GUESS = 5 # penciled-in guess.
	MOVE_ACTION = 6
	GUESS_ACTION = 7  # 0 is unset guess
	NOTE_ACTION = 8
	SET = 9
	
# this encoding could be one-hot, integer, or both:
# need to experiment! 
class Axes(int, Enum): 
	N_AX = 10 #null, nop
	X_AX = 11
	Y_AX = 12
	B_AX = 13 # block
	H_AX = 14 # highlight (where the cursor is)
	G_AX = 15 # same as the Guess action, fwiw

# this is not encoded in the board, just for the python code! 
class Action(Enum):
	UP = 0
	RIGHT = 1
	DOWN = 2
	LEFT = 3
	SET_GUESS = 4
	UNSET_GUESS = 5
	SET_NOTE = 6
	UNSET_NOTE = 7
	NOP = 8


def getActionName(action_type): 
	match action_type: 
		case 0: 
			return "UP"
		case 1: 
			return "RIGHT"
		case 2:
			return "DOWN"
		case 3: 
			return "LEFT"
		case 4:
			return "SET_GUESS"
		case 5: 
			return "UNSET_GUESS"
		case 6:
			return "SET_NOTE"
		case 7:
			return "UNSET_NOTE"
		case _ : 
			return "NOP"
