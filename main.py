import math
import mmap
import torch as th
from torch import nn, optim
import numpy as np
import argparse
import matplotlib.pyplot as plt
import pdb
from ctypes import * # for io
# from multiprocessing import Pool
from functools import partial

import model
from sudoku_gen import Sudoku
from plot_mmap import make_mmf, write_mmap
from constants import *
 

def actionName(act): 
	sact = '-'
	if act == 0: 
		sact = 'up'
	if act == 1: 
		sact = 'right'
	if act == 2:
		sact = 'down'
	if act == 3: 
		sact = 'left'
	if act == 4: 
		sact = 'set guess'
	if act == 5:
		sact = 'unset guess'
	if act == 6:
		sact = 'set note'
	if act == 7:
		sact = 'unset note'
	if act == 8: 
		sact = 'nop'
	return sact

'''
replay_buffer is a list of lists
each sub-list = an episode. 
Eventually need to replace this with a tree for even longer planning ..
'''

class ReplayData: 
	def __init__(self, mat, cursPos, board_enc, new_board,
				  guess, notes, hotnum, hotact, reward, predreward): 
		self.mat = mat # immutable, ref ok.
		self.cursPos = cursPos.copy() # otherwise, you store a ref.
		self.board_enc = board_enc.clone()
		self.new_board = new_board.clone()
		self.guess = guess.clone()
		self.notes = notes.clone()
		self.hotnum = hotnum.clone() # discrete: what was chosen
		self.hotact = hotact.clone()
		self.reward = reward # instant reward
		self.predreward = predreward # what we expected the lt reward to be..
	def setTotalRew(self, treward): 
		self.treward = treward
	def print(self, fd, i,j): 
		fd.write(f'[{i},{j}] cursor {self.cursPos[0]},{self.cursPos[1]}\n')
		sact = actionName(np.argmax(self.hotact))
		num = np.argmax(self.hotnum)
		fd.write(f'\t num:{num} act:{sact} rew:{self.reward}\n')

def updateNotes(cursPos, num, notes): 
	# emulate the behaviour on sudoku.com:
	# if a valid number is placed on the guess board, 
	# eliminate note possbilities accordingly
	# -- within the box 
	i,j = cursPos[0],cursPos[1]
	bi,bj = i - i%3, j - j%3
	for ii in range(3):
		for jj in range(3): 
			notes[bi+ii][bj+jj][num-1] = 0.0
	# -- within the column
	for ii in range(9):
		notes[ii][j][num-1] = 0.0
	# -- within the row
	for jj in range(9):
		notes[i][jj][num-1] = 0.0

def runAction(action, sudoku, cursPos, guess, notes): 
	# run the action, update the world, return the reward.
	i = 0
	num = np.random.choice(10, p=action[i,0:10].detach().numpy())
	act = np.random.choice(9, p=action[i,10:].detach().numpy())
	reward = -0.05
	if act == 0: # up
		cursPos[0] -= 1
	if act == 1: # right
		cursPos[1] += 1
	if act == 2: # down
		cursPos[0] += 1
	if act == 3: # left
		cursPos[1] -= 1
	cursPos[0] = cursPos[0] % 9
	cursPos[1] = cursPos[1] % 9
	
	if act == 4: 
		curr = guess[cursPos[0], cursPos[1]]
		if sudoku.checkIfSafe(cursPos[0], cursPos[1], num) and curr == 0:
			updateNotes(cursPos, num, notes)
			reward = 1 # ultimate goal is to maximize cumulative expected reward
			guess[cursPos[0], cursPos[1]] = num
		else:
			reward = -1
	if act == 5: 
		curr = guess[cursPos[0], cursPos[1]]
		if curr != 0: 
			guess[cursPos[0], cursPos[1]] = 0
		else:
			reward = -0.25
	# no reward/cost for notes -- this has to be algorithmic/inferred
	if act == 6: 
		if notes[cursPos[0], cursPos[1], num-1] == 0:
			notes[cursPos[0], cursPos[1], num-1] = 1.0
		else: 
			reward = -0.25 # penalize redundant actions
	if act == 7: 
		if notes[cursPos[0], cursPos[1], num-1] > 0:
			notes[cursPos[0], cursPos[1], num-1] = 0.0
		else:
			reward = -0.25
	if act == 8: # do nothing. no action.
		reward = -0.06
			
	if True: 
		sact = actionName(act)
		print(f'runAction @ {cursPos[0]},{cursPos[1]}: {sact}; {num}')
	
	hotnum = th.zeros_like(action[i,0:10])
	hotnum[num] = 1.0
	hotact = th.zeros_like(action[i,10:])
	hotact[act] = 1.0
	return hotnum, hotact, reward
	
def runStep(sudoku, cursPos, guess, notes): 
	board_enc = model.encodeBoard(cursPos, sudoku.mat, guess, notes)
	board_enc = th.unsqueeze(board_enc, 0)

	selec = []
	n = 8
	board_encp = board_enc.cuda()
	board_encp = board_encp.expand(n,-1,-1)
	
	latents, action, pred_rew = model.backLatentReward(board_encp)
	
	best = th.argmax(pred_rew[:,-1,1].detach())
	predreward = pred_rew[best,0,0].cpu().detach()
	ltrew = pred_rew[best,-1,1].cpu().detach()
	action = action[best,:,:].cpu().detach() 
	# discard latents? -- re-estimate later (depends on model)
	
	hotnum,hotact,reward = runAction(action, sudoku, cursPos, guess, notes)
	print(f'selected long term reward {ltrew}; got {reward}')
	# runAction updates the cursor, notes, guess.
	new_board = model.encodeBoard(cursPos, sudoku.mat, guess, notes)
	
	d = ReplayData(sudoku.mat, cursPos, board_enc, new_board,
					guess, notes, hotnum, hotact, reward, predreward)
	return d
			
def saveReplayBuffer(replay_buffer):
	fd = open('replay_buffer.txt', 'w')
	for i,episode in enumerate(replay_buffer): 
		for j,e in enumerate(episode):
			e.print(fd, i, j)
	fd.close()
	
	# fd = open('rewardlog.txt', 'w')
	# for e in replay_buffer: 
	# 	fd.write(f'{e.reward}\t{e.predreward}\n')
	# fd.close()
	
def compressReplayBuffer(model, sudoku, replay_buffer): 
	# given input and output, infer latents to produce actions. 
	# see if this action has the same effect as the original
	# if so, replace it. 
	pdb.set_trace()
	to_add = []
	to_remove = []
	for episode in replay_buffer: 
		board_enc = episode[0].board_enc # includes everything! cursPos etc
		new_board = episode[-1].new_board
		
		latents,ap,rp = model.backLatentBoard(board_enc.cuda(), new_board.cuda())
		# check by running.
		p = episode[0]
		cursPos[0] = p.cursPos[0] # deep copy
		cursPos[1] = p.cursPos[1]
		guess = p.guess.clone() # throw away other.
		notes = p.notes.clone()
		replays = []
		for i in range(14): 
			act = ap[0, i, 10:].detach().cpu().numpy()
			if np.max(act) > 0.75: 
				board_encp = model.encodeBoard(cursPos, sudoku.mat, guess, notes)
				board_encp = th.unsqueeze(board_enc, 0)
				
				hotnum, hotact,reward = runAction(ap[i, :], sudoku, cursPos, guess, notes)
				new_boardp = model.encodeBoard(cursPos, sudoku.mat, guess, notes)
				
				d = ReplayData(sudoku.mat, cursPos, board_encp, new_boardp,
					guess, notes, hotnum, hotact, reward, rp[i,0])
				replays.append(d)
				
		if np.sum(np.abs(new_board - new_boardp)) < 0.1: 
			print("!! found a replacement / simplification! !!" )
			for d in replays: 
				to_add.append(replays)
				to_remove.append(episode)
	
	for rem in to_remove: 
		replay_buffer.remove(rem)
	for add in to_add: 
		replay_buffer.append(add)


def makeBatch(b, replay_buffer):
	r = np.random.randint(len(replay_buffer))
	episode = replay_buffer[r]
	
	j = np.random.randint(len(episode)) 
	lst = episode[j:]
	
	actions_batch = th.zeros(latent_cnt, action_dim)
	rewards_batch = th.zeros(latent_cnt, reward_dim)
	for k, d in enumerate(lst):
		actions_batch[k, 0:10] = d.hotnum
		actions_batch[k, 10:] = d.hotact
		rewards_batch[k, 0] = d.reward
	rewards_batch[:,1] = th.cumsum(rewards_batch[:,0], dim=0)
	d = lst[0]
	board_batch = d.board_enc
	d = lst[-1]
	new_board_batch = d.new_board

	return board_batch, new_board_batch, actions_batch, rewards_batch
	
	
if __name__ == '__main__':
	sudoku = Sudoku(9, 25)

	model = model.Racoonizer(
		xfrmr_width = xfrmr_width, 
		world_dim = world_dim,
		latent_cnt = latent_cnt, 
		action_dim = action_dim, 
		reward_dim = reward_dim).cuda()

	# pool = Pool() #defaults to number of available CPU's
	# chunksize = 1
	
	replay_buffer = [] # this ought to be a tree. 
	terminal_buffer = [] # indexes replay_buffer
	puzzles = th.load('puzzles_100000.pt')
	
	fd_board = make_mmf("board.mmap", [batch_size, 82, world_dim])
	fd_new_board = make_mmf("new_board.mmap", [batch_size, 82, world_dim])
	fd_worldp = make_mmf("worldp.mmap", [batch_size, 82, world_dim])
	fd_action = make_mmf("action.mmap", [batch_size, latent_cnt, action_dim])
	fd_actionp = make_mmf("actionp.mmap", [batch_size, latent_cnt, action_dim])
	fd_reward = make_mmf("reward.mmap", [batch_size, latent_cnt, reward_dim])
	fd_rewardp = make_mmf("rewardp.mmap", [batch_size, latent_cnt, reward_dim])

	fd_losslog = open('losslog.txt', 'w')
	uu = 0
		
	for p in range(100): 
		for u in range(50):
			i = np.random.randint(puzzles.shape[0])
			puzzl = puzzles[i, :, :]
			sudoku.setMat(puzzl.numpy())
			
			cursPos = [np.random.randint(9),np.random.randint(9)]
			guess = th.zeros(9, 9) # row, col, digit (zero = no guess)
			notes = th.ones(9, 9, 9) # row, col, (one-hot) digit
			for i in range(9):
				for j in range(9):
					if puzzl[i,j] > 0.0:
						notes[i,j,:] = 0.0 # clear all clue squares
		
			episode = []
			d = runStep(sudoku, cursPos, guess, notes) 
			episode.append(d)
			
			v = 0
			while d.reward >= -0.5 and d.reward <= 0.5 and v < 13:
				d = runStep(sudoku, cursPos, guess, notes)
				episode.append(d)
				v += 1
			replay_buffer.append(episode)

		saveReplayBuffer(replay_buffer)
		
		oldlen = len(replay_buffer)
		compressReplayBuffer(model, sudoku, replay_buffer)
		newlen = len(replay_buffer)
		print(f'replay buffer old {oldlen} to {newlen}')

		# TODO: 
		# -- Start some games from the middle
		# -- Select actions for **more than just reward** 
		#    maybe predictability, estimated information gain, 
		#    some other internal metrics?  
		#    some sort of internally generated progress, 
		#    inclusive of information gain?  
		# -- prune rollouts by total reward: ignore actions that just cost time.
		#    need to avoid degeneracy: sampling the same option over and over
		#    internal novelty reward? 
		# -- ignore equivalences in rollouts: 
		#    model should predict simpler actions!!
		# -- add in option to sample multipe actions?? if they are predictable?
		# -- continue to check the board predictions etc.  
		# -- verify that it's actually converging 
		# -- can memorize the training dataset
		# -- run it on the GPU
		# -- select longer runs for prediction-training
		# -- prune away useless rollouts?
		
		optimizer = optim.AdamW(model.parameters(), lr=5e-4)
		
		for u in range(400): 
			
			board = th.zeros(batch_size, 82, world_dim)
			new_board = th.zeros(batch_size, 82, world_dim)
			actions = th.zeros(batch_size, latent_cnt, action_dim)
			rewards = th.zeros(batch_size, latent_cnt, reward_dim)
			
			makeBatchPartial = partial(makeBatch, replay_buffer=replay_buffer)
			# results = pool.map(makeBatchPartial, range(batch_size))
			results = map(makeBatchPartial, range(batch_size))

			for b, result in enumerate(results):
				board[b, :, :], new_board[b, :, :], actions[b, :, :], rewards[b, :, :] = result
				
			board = board.cuda()
			new_board = new_board.cuda()
			actions = actions.cuda()
			rewards = rewards.cuda()
			latents = model.backLatent(board, new_board, actions, rewards)
			
			wp, ap, rp = model.forward(board, latents)
			loss = th.sum((new_board - wp)**2)*0.05 + \
						th.sum((actions - ap)**2) + \
						th.sum((rewards - rp)**2) 
			loss.backward()
			#th.nn.utils.clip_grad_norm_(model.parameters(), 0.025)
			optimizer.step() 
			
			loss.detach()
			print(loss.cpu().item())
			fd_losslog.write(f'{uu}\t{loss.cpu().item()}\n')
			fd_losslog.flush()
			uu = uu + 1

			if u % 10 == 9: 
				write_mmap(fd_board, board.cpu())
				write_mmap(fd_new_board, new_board.cpu())
				write_mmap(fd_worldp, wp.cpu().detach())
				write_mmap(fd_action, actions.cpu())
				write_mmap(fd_actionp, ap.cpu().detach())
				write_mmap(fd_reward, rewards.cpu())
				write_mmap(fd_rewardp, rp.cpu().detach())

	fd_losslog.close()
