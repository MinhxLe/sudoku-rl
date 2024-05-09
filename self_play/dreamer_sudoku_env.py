'''
Used to work with https://github.com/NM512/dreamerv3-torch/tree/main/envs dreamer code 
Note: no action masking 
'''
import sys 
from pathlib import Path  
from sudoku_env import SudokuEnv
import gym 
import gymnasium 
import torch 
from gymnasium import spaces
from gymnasium.spaces import Discrete
import numpy as np
sys.path.append(str(Path(__file__).resolve().parent.parent))
from sudoku_gen import Sudoku, LoadSudoku
import torch 

         
class NoMaskSudoku(gym.Env):
    '''
    Sudoku to be used with no action masking. Assumes that generated puzzle is solvable
    '''
    def __init__(self, n_blocks: int, percent_filled: float, puzzles_file="satnet_puzzles_100k.pt"):
        self.n_blocks = n_blocks
        self.percent_filled = percent_filled 
        self.board_width = self.n_blocks**2
        # board consists of digits and 0 (for empty cells). Board positions i,j are 0-indexed. 
        # Note that observation is a flattened vector
        self._observation_space = gym.spaces.Box(low=0,high=self.board_width, shape=(self.board_width**2,), dtype=np.uint8)
        self._action_space = gym.spaces.Discrete(self.board_width**3)
       
        self.puzzles_list = torch.load(puzzles_file)
        self._setupGame()

    def _actionTupleToAction(self, action_tuple):
        '''
        Converts (i,j,digit) tuple to an action integer in [0, board_width^3]
        '''
        (i,j,digit) = action_tuple

        return i * (self.board_width**2) + j * (self.board_width) + digit - 1


    def _actionToActionTuple(self, action_num:int):
        '''
        Given an action num in [0, board_width^3), convert to a tuple which represents
            (i,j, digit) where i,j in [0,board_width) and digit in [1,board_width]
        '''
        i = action_num // (self.board_width**2)
        remainder = action_num % (self.board_width**2)
        j = remainder // self.board_width 
        digit = (remainder % self.board_width) + 1

        return (i, j, digit)       
    
    def _setupGame(self):
        sudoku = LoadSudoku(self.board_width, self.puzzles_list)
        self.sudoku = sudoku
        self.sudoku.fillValues()


    def getReward(self):
        '''
        Returns 1 if game is valid solved, -1 if game is incorrectly solved or empty cells but no further playable actions,
          0 if empty cells left and playable moves
        '''
        board = self.sudoku.mat 

        row_seen_sets = [set() for _ in range(self.board_width)]
        col_seen_sets = [set() for _ in range(self.board_width)]
        box_seen_sets = [set() for _ in range(self.board_width)]

        for i in range(self.board_width):
            for j in range(self.board_width):
                val = board[i][j]
                # Empty cell exists
                if val == 0: 
                    return 0

                # check row 
                if val in row_seen_sets[i]:
                    return -1 
                row_seen_sets[i].add(val)
                # check col 
                if val in col_seen_sets[j]:
                    return -1 
                col_seen_sets[j].add(val) 

                # check box 
                box_idx = (i // self.n_blocks) * self.n_blocks + (j // self.n_blocks)
                if val in box_seen_sets[box_idx]:
                    return -1 
                box_seen_sets[box_idx].add(val)
        
        # If game correctly solved        
        return 1 	
    


    def _step(self, action: int):
        '''
        action: (int) Later converted to a tuple (i,j,act) where i,j represent board indices and act 
            represents digit placed 
        
        Returns board, is_done, reward
        '''
        (i,j,digit) = self._actionToActionTuple(action)
        self.sudoku.makeMove(i,j,digit)

        # reward is 1 if win, -1 if lose, 0 if empty cell exist
        reward = self.getReward()
        is_done = (reward != 0)

        return self.sudoku.mat.flatten(), reward, is_done, False, {}  
    
    def reset(self):
        self._setupGame()
        return self.sudoku.mat.flatten(), {} 


class DreamerSudokuEnv(NoMaskSudoku):
    """
    Written to work with https://github.com/NM512/dreamerv3-torch/tree/main/envs
    """
    def __init__(self, config):
        n_blocks = config.get("n_blocks", 3)
        percent_filled = config.get("percent_filled", 0.75)
        puzzles_file = config.get("puzzles_file", "break.pt")

        super().__init__(n_blocks, percent_filled, puzzles_file)
        self._skip_env_checking = False

    @property
    def observation_space(self):
        spaces = {
            "board": gym.spaces.Box(low=0,high=self.board_width, shape=(self.board_width**2,), dtype=np.uint8),
            "is_first": gym.spaces.Box(-np.inf, np.inf, (1,), dtype=np.uint8),
            "is_last": gym.spaces.Box(-np.inf, np.inf, (1,), dtype=np.uint8),
            "is_terminal": gym.spaces.Box(-np.inf, np.inf, (1,), dtype=np.uint8)
        }
        return gym.spaces.Dict(spaces)

    @property
    def action_space(self):
        action_space = self._action_space
        action_space.discrete = True
        return action_space

    def reset(self):
        board, info = super().reset()
        obs = {
            "board": board, 
            "is_first": True,
            "is_last": False,
            "is_terminal": False,
        }
        return obs 
    
    def step(self, action):
        board, reward, done, _, info = self._step(action)
        reward = np.float32(reward)
        obs = {
            "board": board,
            "is_first": False,
            "is_last": done,
            "is_terminal": done
        }
        return obs, reward, done, info