import math
import random
import argparse
import time
import os
import numpy as np
import torch
from torch import nn, optim
from torch.utils.data import DataLoader, Dataset
import pdb
from termcolor import colored
import pickle
import matplotlib.pyplot as plt
import sparse_encoding
from gracoonizer import Gracoonizer
from sudoku_gen import Sudoku
from plot_mmap import make_mmf, write_mmap
from netdenoise import NetDenoise
from test_gtrans import getTestDataLoaders, SimpleMLP
from constants import *
from type_file import Action, Axes, getActionName
from l1attn_sparse_cuda import expandCoo
import psgd 
	# https://sites.google.com/site/lixilinx/home/psgd
	# https://github.com/lixilinx/psgd_torch/issues/2

def runAction(sudoku, puzzl_mat, guess_mat, curs_pos, action:int, action_val:int): 
	
	# run the action, update the world, return the reward.
	# act = b % 4
	reward = -0.05
	if action == Action.UP.value : 
		curs_pos[0] -= 1
	if action == Action.RIGHT.value: 
		curs_pos[1] += 1
	if action == Action.DOWN.value: 
		curs_pos[0] += 1
	if action == Action.LEFT.value:
		curs_pos[1] -= 1
	# clip (rather than wrap) cursor position
	for i in range(2): 
		if curs_pos[i] < 0: 
			reward = -0.5
			curs_pos[i] = 0
		if curs_pos[i] >= SuN: 
			reward = -0.5
			curs_pos[i] = SuN - 1
		
	# curs_pos[0] = curs_pos[0] % SuN # wrap at the edges; 
	# curs_pos[1] = curs_pos[1] % SuN # works for negative nums
	
	if action == Action.SET_GUESS.value:
		clue = puzzl_mat[curs_pos[0], curs_pos[1]]
		curr = guess_mat[curs_pos[0], curs_pos[1]]
		sudoku.setMat(puzzl_mat + guess_mat) # so that checkIfSafe works properly.
		if clue == 0 and curr == 0 and sudoku.checkIfSafe(curs_pos[0], curs_pos[1], action_val):
			# updateNotes(curs_pos, action_val, notes)
			reward = 1
			guess_mat[curs_pos[0], curs_pos[1]] = action_val
		else:
			reward = -1
	if action == Action.UNSET_GUESS.value:
		curr = guess_mat[curs_pos[0], curs_pos[1]]
		if curr != 0: 
			guess_mat[curs_pos[0], curs_pos[1]] = 0
			reward = -1 # must exactly cancel, o/w best strategy is to simply set/unset guess repeatedly.
		else:
			reward = -1.25
			
	if False: 
		print(f'runAction @ {curs_pos[0]},{curs_pos[1]}: {action}:{action_val}')
	
	return reward

	
def encodeBoard(sudoku, puzzl_mat, guess_mat, curs_pos, action, action_val):  
	'''
	Encodes the current board state and encodes the given action,
		runs the action, and then encodes the new board state.
		Also returns a mask matrix (#nodes by #nodes) which represents parent/child relationships
		which defines the attention mask used in the transformer heads

	The board and action nodes have the same encoding- contains one hot of node type and node value
	
	Returns:
	board encoding: Shape (#board nodes x world_dim)
	action encoding: Shape (#action nodes x world_dim)
	new board encoding: Shape (#newboard nodes x world_dim)
	'''
	nodes, reward_loc,_ = sparse_encoding.sudokuToNodes(puzzl_mat, guess_mat, curs_pos, action, action_val, 0.0)
	benc,coo,a2a = sparse_encoding.encodeNodes(nodes)
	
	reward = runAction(sudoku, puzzl_mat, guess_mat, curs_pos, action, action_val)
	
	nodes, reward_loc,_ = sparse_encoding.sudokuToNodes(puzzl_mat, guess_mat, curs_pos, action, action_val, reward) # action_val doesn't matter
	newbenc,coo,a2a = sparse_encoding.encodeNodes(nodes)
	
	return benc, newbenc, coo, a2a, reward, reward_loc
	
def encode1DBoard():  
	# simple 1-D version of sudoku. 
	puzzle = np.arange(1, 10)
	mask = np.random.randint(0,3,9)
	puzzle = puzzle * (mask > 0)
	curs_pos = np.random.randint(0,9)
	action = 4
	action_val = np.random.randint(0,9)
	guess_mat = np.zeros((9,))

	nodes, reward_loc,_ = sparse_encoding.sudoku1DToNodes(puzzle, guess_mat, curs_pos, action, action_val, 0.0)
	benc,coo,a2a = sparse_encoding.encodeNodes(nodes)
	
	# run the action. 
	reward = -1
	if puzzle[action_val-1] == 0 and puzzle[curs_pos] == 0: 
		guess_mat[curs_pos] = action_val
		reward = 1
	
	nodes, reward_loc,_ = sparse_encoding.sudoku1DToNodes(puzzle, guess_mat, curs_pos, action, action_val, reward) # action_val doesn't matter
	newbenc,coo,a2a = sparse_encoding.encodeNodes(nodes)
	
	return benc, newbenc, coo, a2a, reward, reward_loc

	
def enumerateActionList(n:int): 
	action_types = []
	action_values = []
	# directions
	for at in [0,1,2,3]: 
		action_types.append(at)
		action_values.append(0)
	at = Action.SET_GUESS.value
	for av in range(SuN):
		action_types.append(at)
		action_values.append(av+1)
	# unset guess action
	action_types.append( Action.UNSET_GUESS.value )
	action_values.append( 0 )
	
	nactions = len(action_types)
	if len(action_types) < n: 
		rep = n // len(action_types) + 1
		action_types = action_types * rep
		action_values = action_values * rep
	if len(action_types) > n: 
		action_types = action_types[:n]
		action_values = action_values[:n]
	return action_types,action_values
	
def sampleActionList(n:int): 
	# this is slow but whatever, only needs to run once
	action_types = []
	action_values = []
	possible_actions = [ 0,1,2,3,4,4,4,4,4,5,5 ] # FIXME
	for i in range(n): 
		action = possible_actions[np.random.randint(len(possible_actions))]
		actval = 0
		if action == Action.SET_GUESS.value: 
			actval = np.random.randint(1,10)
		action_types.append(action)
		action_values.append(actval)
	
	return action_types,action_values


def enumerateBoards(puzzles, n, possible_actions=[], min_dist=1, max_dist=1): 
	'''
	Parameters:
	n: (int) Number of samples to generate
	min_dist: (int) Represents the min distance travelled.
	max_dist: (int) Represents the max distance travelled (inclusive)

	Returns:
	orig_board_enc: (tensor) Shape (N x #board nodes x 20), all the initial board encodings
	new_board_enc: (tensor) Shape (N x #board nodes x 20), all of the resulting board encodings due to actions
	rewards: (tensor) Shape (N,) Rewards of each episode 
	'''
	# changing the strategy: for each board, do all possible actions. 
	# this serves as a stronger set of constraints than random enumeration.
	try: 
		orig_board_enc = torch.load(f'orig_board_enc_{n}.pt')
		new_board_enc = torch.load(f'new_board_enc_{n}.pt')
		rewards_enc = torch.load(f'rewards_enc_{n}.pt')
		# need to get the coo, a2a, etc variables - so run one encoding.
		n = 1
	except Exception as error:
		print(colored(f"could not load precomputed data {error}", "red"))
	
	# action_types,action_values = enumerateActionList(n)
	action_types,action_values = sampleActionList(n)
		
	sudoku = Sudoku(SuN, SuK)
	orig_boards = [] 
	new_boards = []
	actions = []
	rewards = torch.zeros(n, dtype=g_dtype)
	curs_pos_b = torch.randint(SuN, (n,2),dtype=int)
	
	# for half the boards, select only open positions. 
	for i in range( n // 2 ): 
		puzzl = puzzles[i, :, :]
		while puzzl[curs_pos_b[i,0], curs_pos_b[i,1]] > 0: 
			curs_pos_b[i,:] = torch.randint(SuN, (1,2),dtype=int)
	
	for i,(at,av) in enumerate(zip(action_types,action_values)):
		puzzl = puzzles[i, :, :].numpy()
		# move half the clues to guesses (on average)
		# to force generalization over both!
		mask = np.random.randint(0,2, (SuN,SuN)) == 1
		guess_mat = puzzl * mask
		puzzl_mat = puzzl * (1-mask)
		curs_pos = curs_pos_b[i, :] # see above.
		
		benc,newbenc,coo,a2a,reward,reward_loc = encodeBoard(sudoku, puzzl_mat, guess_mat, curs_pos, at, av )
		# benc,newbenc,coo,a2a,reward,reward_loc = encode1DBoard()
		orig_boards.append(benc)
		new_boards.append(newbenc)
		rewards[i] = reward
		
	if n > 1: 
		orig_board_enc = torch.stack(orig_boards)
		new_board_enc = torch.stack(new_boards)
		rewards_enc = rewards
		torch.save(orig_board_enc, f'orig_board_enc_{n}.pt')
		torch.save(new_board_enc, f'new_board_enc_{n}.pt')
		torch.save(rewards_enc, f'rewards_enc_{n}.pt')
		
	return orig_board_enc, new_board_enc, coo, a2a, rewards_enc, reward_loc

def trainValSplit(data_matrix: torch.Tensor, num_validate):
	'''
	Split data matrix into train and val data matrices
	data_matrix: (torch.tensor) Containing rows of data
	num_validate: (int) If provided, is the number of rows in the val matrix
	
	This is OK wrt constraints, as the split is non-stochastic in the order.
	'''
	num_samples = data_matrix.size(0)
	if num_samples <= 1:
		raise ValueError(f"data_matrix needs to be a tensor with more than 1 row")
	
	training_data = data_matrix[:-num_validate]
	eval_data = data_matrix[-num_validate:]
	return training_data, eval_data


class SudokuDataset(Dataset):
	'''
	Dataset where the ith element is a sample containing orig_board_enc,
		new_board_enc, action_enc, graph_mask, board_reward
	'''
	def __init__(self, orig_boards, new_boards, rewards):
		self.orig_boards = orig_boards
		self.new_boards = new_boards
		self.rewards = rewards 

	def __len__(self):
		return self.orig_boards.size(0)

	def __getitem__(self, idx):
		sample = {
			'orig_board' : self.orig_boards[idx],
			'new_board' : self.new_boards[idx], 
			'reward' : self.rewards[idx].item(),
		}
		return sample


def getDataLoaders(puzzles, num_samples, num_validate):
	'''
	Returns a pytorch train and test dataloader
	'''
	data_dict, coo, a2a, reward_loc = getDataDict(puzzles, num_samples, num_validate)
	train_dataset = SudokuDataset(data_dict['train_orig_board'],
											data_dict['train_new_board'], 
											data_dict['train_rewards'])

	test_dataset = SudokuDataset(data_dict['test_orig_board'],
										data_dict['test_new_board'], 
										data_dict['test_rewards'])

	train_dataloader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
	test_dataloader = DataLoader(test_dataset, batch_size=batch_size, shuffle=True)

	return train_dataloader, test_dataloader, coo, a2a, reward_loc


def getDataDict(puzzles, num_samples, num_validate):
	'''
	Returns a dictionary containing training and test data
	'''
	orig_board, new_board, coo, a2a, rewards, reward_loc = enumerateBoards(puzzles, num_samples)
	print(orig_board.shape, new_board.shape, rewards.shape)
	train_orig_board, test_orig_board = trainValSplit(orig_board, num_validate)
	train_new_board, test_new_board = trainValSplit(new_board, num_validate)
	train_rewards, test_rewards = trainValSplit(rewards, num_validate)

	dataDict = {
		'train_orig_board': train_orig_board,
		'train_new_board': train_new_board,
		'train_rewards': train_rewards,
		'test_orig_board': test_orig_board,
		'test_new_board': test_new_board,
		'test_rewards': test_rewards
	}
	return dataDict, coo, a2a, reward_loc

def getMemoryDict():
	fd_board = make_mmf("board.mmap", [batch_size, token_cnt, world_dim])
	fd_new_board = make_mmf("new_board.mmap", [batch_size, token_cnt, world_dim])
	fd_boardp = make_mmf("boardp.mmap", [batch_size, token_cnt, world_dim])
	fd_reward = make_mmf("reward.mmap", [batch_size, reward_dim])
	fd_rewardp = make_mmf("rewardp.mmap", [batch_size, reward_dim])
	fd_attention = make_mmf("attention.mmap", [2, token_cnt, token_cnt, n_heads])
	fd_wqkv = make_mmf("wqkv.mmap", [n_heads*2,2*xfrmr_dim,xfrmr_dim])
	memory_dict = {'fd_board':fd_board, 'fd_new_board':fd_new_board, 'fd_boardp':fd_boardp,
					 'fd_reward': fd_reward, 'fd_rewardp': fd_rewardp, 'fd_attention': fd_attention,
					  'fd_wqkv':fd_wqkv }
	return memory_dict


def getLossMask(board_enc, device):
	'''
	mask off extra space for passing info between layers
	'''
	loss_mask = torch.ones(1, board_enc.shape[1], board_enc.shape[2], device=device,dtype=g_dtype)
	for i in range(11,20):
		loss_mask[:,:,i] *= 0.001 # semi-ignore the "latents"
	return loss_mask 

def getOptimizer(optimizer_name, model, lr=2.5e-4, weight_decay=0):
	if optimizer_name == "adam": 
		optimizer = optim.Adam(model.parameters(), lr=lr)
	elif optimizer_name == 'adamw':
		optimizer = optim.AdamW(model.parameters(), lr=lr, amsgrad=True)
	elif optimizer_name == 'sgd':
		optimizer = optim.SGD(model.parameters(), lr=lr*1e-3)
	else: 
		optimizer = psgd.LRA(model.parameters(),lr_params=0.01,lr_preconditioner=0.01, momentum=0.9,\
			preconditioner_update_probability=0.1, exact_hessian_vector_product=False, rank_of_approximation=20, grad_clip_max_norm=5.0)
	return optimizer 

def updateMemory(memory_dict, pred_dict): 
	'''
	Updates memory map with predictions.

	Args:
	memory_dict (dict): Dictionary containing memory map file descriptors.
	pred_dict (dict): Dictionary containing predictions.

	Returns:
	None
	'''
	write_mmap(memory_dict['fd_board'], pred_dict['old_board'].cpu())
	write_mmap(memory_dict['fd_new_board'], pred_dict['new_board'].cpu())
	write_mmap(memory_dict['fd_boardp'], pred_dict['new_state_preds'].cpu().detach())
	write_mmap(memory_dict['fd_reward'], pred_dict['rewards'].cpu())
	write_mmap(memory_dict['fd_rewardp'], pred_dict['reward_preds'].cpu().detach())
	if 'a1' in pred_dict and 'a2' in pred_dict:
		if (pred_dict['a1'] is not None) and (pred_dict['a2'] is not None):
			write_mmap(memory_dict['fd_attention'], torch.stack((pred_dict['a1'], pred_dict['a2']), 0))
	if (pred_dict['w1'] is not None) and (pred_dict['w2'] is not None):
		write_mmap(memory_dict['fd_wqkv'], torch.stack((pred_dict['w1'], pred_dict['w2']), 0))
	return 

def train(args, memory_dict, model, train_loader, optimizer, hcoo, reward_loc, uu):
	# model.train()
	sum_batch_loss = 0.0

	for batch_idx, batch_data in enumerate(train_loader):
		old_board, new_board, rewards = [t.to(args["device"]) for t in batch_data.values()]
		
		# scale down the highlight, see if the model can learn.. 
		# uu_scl = 1.0 - uu / args["NUM_ITERS"]
		# uu_scl = uu_scl / 10
		# old_board[:,:,Axes.H_AX.value] = old_board[:,:,Axes.H_AX.value] * uu_scl
		# new_board[:,:,Axes.H_AX.value] = new_board[:,:,Axes.H_AX.value] * uu_scl
		# appears much harder! 

		pred_data = {}
		if optimizer_name != 'psgd': 
			optimizer.zero_grad()
			new_state_preds,w1,w2 = \
				model.forward(old_board, hcoo, uu, None)
			reward_preds = new_state_preds[:,reward_loc, 32+26]
			pred_data = {'old_board':old_board, 'new_board':new_board, 'new_state_preds':new_state_preds,
					  		'rewards': rewards*5, 'reward_preds': reward_preds,
							'w1':w1, 'w2':w2}
			# new_state_preds dimensions bs,t,w 
			loss = torch.sum((new_state_preds[:,:,33:64] - new_board[:,:,1:32])**2)
			# adam is unstable -- attempt to stabilize?
			torch.nn.utils.clip_grad_norm_(model.parameters(), 0.8)
			loss.backward()
			optimizer.step() 
			print(loss.detach().cpu().item())
		else: 
			# psgd library internally does loss.backwards and zero grad
			def closure():
				nonlocal pred_data
				new_state_preds,w1,w2 = model.forward(old_board, hcoo, uu, None)
				reward_preds = new_state_preds[:,reward_loc, 32+26]
				pred_data = {'old_board':old_board, 'new_board':new_board, 'new_state_preds':new_state_preds,
								'rewards': rewards*5, 'reward_preds': reward_preds,
								'w1':w1, 'w2':w2}
				loss = torch.sum((new_state_preds[:,:,33:64] - new_board[:,:,1:32])**2) + \
					sum( \
					[torch.sum(1e-4 * torch.rand_like(param,dtype=g_dtype) * param * param) for param in model.parameters()])
					# this was recommended by the psgd authors to break symmetries w a L2 norm on the weights. 
				return loss
			loss = optimizer.step(closure)
		
		lloss = loss.detach().cpu().item()
		print(lloss)
		args["fd_losslog"].write(f'{uu}\t{lloss}\n')
		args["fd_losslog"].flush()
		uu = uu + 1
		
		if uu % 1000 == 999: 
			model.save_checkpoint(f"checkpoints/racoonizer_{uu//1000}.pth")

		sum_batch_loss += lloss
		if batch_idx % 25 == 0:
			updateMemory(memory_dict, pred_data)
			pass 
	
	# add epoch loss
	avg_batch_loss = sum_batch_loss / len(train_loader)
	return uu
	
	
def validate(args, model, test_loader, optimzer_name, hcoo, uu):
	model.eval()
	sum_batch_loss = 0.0
	with torch.no_grad():
		for batch_data in test_loader:
			old_board, new_board, rewards = [t.to(args["device"]) for t in batch_data.values()]
			new_state_preds,w1,w2 = model.forward(old_board, hcoo, uu, None)
			reward_preds = new_state_preds[:,reward_loc, 32+26]
			loss = torch.sum((new_state_preds[:,:,33:64] - new_board[:,:,1:32])**2)
			lloss = loss.detach().cpu().item()
			print(f'v{lloss}')
			fd_losslog.write(f'{uu}\t{lloss}\n')
			fd_losslog.flush()
			sum_batch_loss += loss.cpu().item()
	
	avg_batch_loss = sum_batch_loss / len(test_loader)
			
	return 
	
def trainQfun(rollouts_board, rollouts_reward_sum, rollouts_action, nn, memory_dict, model, qfun, hcoo, reward_loc, locs):
	n_time = rollouts_board.shape[0]
	n_roll = rollouts_board.shape[1]
	n_tok = rollouts_board.shape[2]
	width = rollouts_board.shape[3]
	pred_data = {}
	for uu in range(nn): 
		indx = torch.randint(0,n_roll,(batch_size,))
		boards = rollouts_board[:,indx,:,:].squeeze()
		reward_sum = rollouts_reward_sum[:,indx].squeeze()
		actions = rollouts_action[:,indx,:].squeeze()
		
		lin = torch.arange(batch_size)
		tindx = torch.randint(0, 150, (batch_size,))
		boards = rollouts_board[tindx,lin,:].squeeze().float()
		# expnd boards
		boards = torch.cat((boards, torch.zeros(batch_size, n_tok, width)), dim=2)
		# remove the layer encoding; will be replaced in the model.
		boards[:,:,0] = 0
		boards = torch.round(boards * 4.0) / 4.0
		reward_sum = rollouts_reward_sum[tindx,lin].squeeze()
		actions = rollouts_action[tindx,lin,:].squeeze()
		
		# sparse_encoding.decodeNodes("", boards[0,:,:].squeeze().float(), locs)
		# do we even need to encode the action? 
		# it should not have changed!  
		# for j in range(batch_size): 
		# 	at = actions[j,0]
		# 	av = actions[j,1]
		# 	aenc = sparse_encoding.encodeActionNodes(at, av)
		# 	s = aenc.shape[0]
		# 	print(torch.sum((boards[j,0:s,:] - aenc)**2).item())
			
		boards = boards.cuda()
		reward_sum = reward_sum.cuda()
		with torch.no_grad(): 
			model_boards,_,_ = model.forward(boards,hcoo,0,None)
			
		def closure(): 
			nonlocal pred_data
			qfun_boards,_,_ = qfun.forward(model_boards,hcoo,i,None)
			reward_preds = qfun_boards[:,reward_loc, 32+26]
			pred_data = {'old_board':boards, 'new_board':model_boards, 'new_state_preds':qfun_boards,
								'rewards': reward_sum, 'reward_preds': reward_preds,
								'w1':None, 'w2':None}
			loss = torch.sum((reward_sum - reward_preds)**2) + \
				sum([torch.sum(1e-4 * torch.rand_like(param,dtype=g_dtype) * param * param) for param in qfun.parameters()])
			return loss
		
		loss = optimizer.step(closure)
		lloss = loss.detach().cpu().item()
		print(lloss)
		args["fd_losslog"].write(f'{uu}\t{lloss}\n')
		args["fd_losslog"].flush()
		if uu % 25 == 0:
			updateMemory(memory_dict, pred_data)
			
		if uu % 1000 == 999: 
			qfun.save_checkpoint(f"checkpoints/quailizer_{uu//1000}.pth")
		
	
class ANode: 
	def __init__(self, typ, val, reward, board_enc):
		self.action_type = typ
		self.action_value = val
		self.kids = []
		self.reward = reward
		# board_enc is the *result* of applying the action.
		self.board_enc = board_enc
		
	def addKid(self, node): 
		self.kids.append(node)
		
	def print(self, indent): 
		color = "black"
		if self.reward > 0: 
			color = "green"
		if self.reward < -1.0: 
			color = "red"
		print(colored(f"{indent}{self.action_type},{self.action_value},{self.reward}", color))
		# indent = indent + " "
		for k in self.kids: 
			k.print(indent)

def plot_tensor(v, name, lo, hi):
	cmap_name = 'seismic'
	fig, axs = plt.subplots(1, 1, figsize=(12,6))
	data = np.linspace(lo, hi, v.shape[0] * v.shape[1])
	data = np.reshape(data, (v.shape[0], v.shape[1]))
	im = axs.imshow(data, cmap = cmap_name)
	plt.colorbar(im, ax=axs)
	im.set_data(v)
	axs.set_title(name)
	axs.tick_params(bottom=True, top=True, left=True, right=True)
	plt.show()
	
def evaluateActions(model, board, hcoo, depth, reward_loc, locs): 
	# clean up the boards
	board = np.round(board * 4.0) / 4.0
	board = torch.tensor(board).cuda()
	bs = board.shape[0]
	ntok = board.shape[1]
	width = board.shape[2]
	# plot_tensor(board.detach().cpu().numpy().T, "board", -4.0, 4.0)
		
	action_types,action_values = enumerateActionList(9+4+1)
	nact = len(action_types)

	# make a batch with the new actions & replicated boards
	board = board.unsqueeze(1)
	new_boards = board.repeat(1, nact, 1, 1 )
	for i,(at,av) in enumerate(zip(action_types,action_values)):
		aenc = sparse_encoding.encodeActionNodes(at, av) # only need one eval!
		s = aenc.shape[0]
		new_boards[:, i, 0:s, :] = aenc.cuda()

	new_boards = new_boards.reshape(bs * nact, ntok, width)
	boards_pred,_,_ = model.forward(new_boards,hcoo,0,None)
	boards_pred = boards_pred.detach().reshape(bs, nact, ntok, width)
	reward_pred = boards_pred[:, :,reward_loc, 32+26].clone().squeeze()
	# copy over the beginning structure - needed!
	# this will have to be changed for longer-term memory TODO
	boards_pred[:,:,:,1:32] = boards_pred[:,:,:,33:]
	boards_pred[:,:,:,32:] = 0 # don't mess up forward computation
	boards_pred[:,:,reward_loc, 26] = 0 # it's a resnet - reset the reward.

	mask = torch.clip(reward_pred + 1.0, 0.0, 10.0) # reward-weighted.
	mask = mask / torch.sum(mask, 1).unsqueeze(1).expand(-1,14)
	indx = torch.multinomial(mask, 1).squeeze()
	lin = torch.arange(0,bs)
	boards_pred_np = boards_pred[lin,indx,:,:].detach().squeeze().cpu().numpy()
	reward_np = reward_pred[lin,indx]
	
	action_node_new = []
	indent = " " # * depth
	for j in range(bs):
		at = action_types[indx[j]]
		av = action_values[indx[j]]
		an = ANode(at, av, reward_np[j], boards_pred_np[j,:,:].squeeze())
		action_node_new.append(an)
		if j == 0: 
			print(f"{indent}action {getActionName(at)} {av} reward {reward_np[0].item()}")
	sparse_encoding.decodeNodes(indent, boards_pred_np[0,:,:], locs)
		
	return boards_pred_np, action_node_new

	
def evaluateActionsRecurse(model, puzzles, hcoo, nn, fname):
	bs = 96
	pi = np.random.randint(0, puzzles.shape[0], (nn,bs))
	anode_list = []
	sudoku = Sudoku(SuN, SuK)
	for i in range(nn):
		puzzl_mat = np.zeros((bs,SuN,SuN))
		for j in range(bs):
			puzzl_mat[j,:,:] = puzzles[pi[i,j],:,:].numpy()
		guess_mat = np.zeros((bs,SuN,SuN))
		curs_pos = torch.randint(SuN, (bs,2),dtype=int)
		print("-- initial state --")
		sudoku.printSudoku("", puzzl_mat[0,:,:], guess_mat[0,:,:], curs_pos[0,:])
		print(f"curs_pos {curs_pos[0,0]},{curs_pos[0,1]}")
		print(colored("-----", "green"))

		board = torch.zeros(bs,token_cnt,world_dim)
		for j in range(bs):
			nodes,reward_loc,locs = sparse_encoding.sudokuToNodes(puzzl_mat[j,:,:], guess_mat[j,:,:], curs_pos[j,:], 0, 0, 0.0) # action will be replaced.
			board[j,:,:],_,_ = sparse_encoding.encodeNodes(nodes)
		
		rollouts_board = torch.zeros(257, bs, token_cnt, 32, dtype=torch.float16)
		rollouts_reward = torch.zeros(257, bs)
		rollouts_action = torch.zeros(257, bs, 2, dtype=int)
		rollouts_board[0,:,:,:] = board[:,:,:32]
		board = board.numpy()
		for j in range(256): 
			board_new, action_node_new = evaluateActions(model, board, hcoo, 0, reward_loc,locs)
			board = board_new
			rollouts_board[j+1,:,:,:] = torch.tensor(board[:,:,:32])
			for k in range(bs):
				rollouts_reward[j+1,k] = action_node_new[k].reward
				rollouts_action[j+1,k, 0] = action_node_new[k].action_type
				rollouts_action[j+1,k, 1] = action_node_new[k].action_value

		torch.save(rollouts_board, f'rollouts/rollouts_board_{i}.pt')
		torch.save(rollouts_reward, f'rollouts/rollouts_reward_{i}.pt')
		torch.save(rollouts_action, f'rollouts/rollouts_action_{i}.pt')

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description="Train sudoku world model")
	parser.add_argument('-c', action='store_true', help='clear, start fresh')
	parser.add_argument('-e', action='store_true', help='evaluate')
	parser.add_argument('-t', action='store_true', help='train')
	parser.add_argument('-q', action='store_true', help='train Q function')
	parser.add_argument('-r', type=int, default=1, help='rollout file number')
	cmd_args = parser.parse_args()
	
	puzzles = torch.load(f'puzzles_{SuN}_500000.pt')
	NUM_TRAIN = batch_size * 1800 
	NUM_VALIDATE = batch_size * 300
	NUM_SAMPLES = NUM_TRAIN + NUM_VALIDATE
	NUM_ITERS = 100000
	device = torch.device('cuda:0') 
	# can override with export CUDA_VISIBLE_DEVICES=1 
	torch.set_float32_matmul_precision('high')
	fd_losslog = open('losslog.txt', 'w')
	args = {"NUM_TRAIN": NUM_TRAIN, "NUM_VALIDATE": NUM_VALIDATE, "NUM_SAMPLES": NUM_SAMPLES, "NUM_ITERS": NUM_ITERS, "device": device, "fd_losslog": fd_losslog}
	
	# get our train and test dataloaders
	train_dataloader, test_dataloader, coo, a2a, reward_loc = getDataLoaders(puzzles, args["NUM_SAMPLES"], args["NUM_VALIDATE"])
	# print(reward_loc)
	# print(coo)
	
	# first half of heads are kids to parents
	kids2parents, dst_mxlen_k2p, _ = expandCoo(coo)
	# swap dst and src
	coo_ = torch.zeros_like(coo) # type int32: indexes
	coo_[:,0] = coo[:,1]
	coo_[:,1] = coo[:,0]
	parents2kids, dst_mxlen_p2k, _ = expandCoo(coo_)
	# and self attention (intra-token attention ops) -- either this or add a second MLP layer. 
	coo_ = torch.arange(token_cnt).unsqueeze(-1).tile([1,2])
	self2self, dst_mxlen_s2s, _ = expandCoo(coo_)
	# add global attention
	all2all = torch.Tensor(a2a); 
	# coo_ = torch.zeros((token_cnt**2-token_cnt, 2), dtype=int)
	# k = 0
	# for i in range(token_cnt): 
	# 	for j in range(token_cnt): 
	# 		if i != j: # don't add the diagonal.
	# 			coo_[k,:] = torch.tensor([i,j],dtype=int)
	# 			k = k+1
	# all2all, dst_mxlen_a2a, _ = expandCoo(coo_)
	kids2parents = kids2parents.cuda()
	parents2kids = parents2kids.cuda()
	self2self = self2self.cuda()	
	all2all = all2all.cuda()
	hcoo = [(kids2parents,dst_mxlen_k2p), (parents2kids,dst_mxlen_p2k), \
		(self2self, dst_mxlen_s2s), all2all]
	
	# allocate memory
	memory_dict = getMemoryDict()
	
	# define model 
	model = Gracoonizer(xfrmr_dim=xfrmr_dim, world_dim=world_dim, n_heads=n_heads, n_layers=8).to(device) 
	model.printParamCount()

	if cmd_args.c: 
		print(colored("not loading any model weights.", "blue"))
	else:
		try:
			checkpoint_dir = 'checkpoints/'
			files = os.listdir(checkpoint_dir)
			files = [os.path.join(checkpoint_dir, f) for f in files]
			# Filter out directories and only keep files
			files = [f for f in files if os.path.isfile(f)]
			if not files:
				raise ValueError("No files found in the checkpoint directory")
			# Find the most recently modified file
			latest_file = max(files, key=os.path.getmtime)
			print(colored(latest_file, "green"))
			model.load_checkpoint(latest_file)
			print(colored("loaded model checkpoint", "blue"))
			time.sleep(1)
		except Exception as error:
			print(colored(f"could not load model checkpoint {error}", "red"))
	
	optimizer_name = "psgd" # adam, adamw, psgd, or sgd
	optimizer = getOptimizer(optimizer_name, model)

	if cmd_args.e: 
		instance = cmd_args.r
		fname = f"rollouts_{instance}.pickle"
		if True:
			anode_list = evaluateActionsRecurse(model, puzzles, hcoo, 250, fname)
		else:
			anode_list = []
			with open(fname, 'rb') as handle:
				print(colored(f"loading rollouts from {fname}", "blue"))
				anode_list = pickle.load(handle)
	
			# need to sum the rewards along each episode - infinite horizon reward.
			def sumRewards(an):
				reward = an.reward
				rewards = [sumRewards(k) for k in an.kids]
				if len(rewards) > 0:
					reward = reward + max(rewards)
				an.reward = reward
				return reward
			for an in anode_list:
				sumRewards(an)
				an.print("")
				
	if cmd_args.q: 
		bs = 96
		nfiles = 55
		rollouts_board = torch.zeros(257, bs*nfiles, token_cnt, 32, dtype=torch.float16)
		rollouts_reward = torch.zeros(257, bs*nfiles)
		rollouts_action = torch.zeros(257, bs*nfiles, 2, dtype=int)
		
		for i in range(nfiles): 
			r_board = torch.load(f'rollouts/rollouts_board_{i}.pt')
			r_reward = torch.load(f'rollouts/rollouts_reward_{i}.pt')
			r_action = torch.load(f'rollouts/rollouts_action_{i}.pt')
			rollouts_board[:,bs*i:bs*(i+1),:,:] = r_board
			rollouts_reward[:,bs*i:bs*(i+1)] = r_reward
			rollouts_action[:,bs*i:bs*(i+1),:] = r_action
			
		# plot one of the reward backward cumsums. no discount!
		ro_reward_sum = torch.cumsum(torch.flip(rollouts_reward, [0,]), 0)
		ro_reward_sum = torch.flip(ro_reward_sum, [0,])
		reward_mean = torch.sum(ro_reward_sum, 1) / (bs*nfiles)
		ro_reward_sum = ro_reward_sum - \
			reward_mean.unsqueeze(1).expand(-1,bs*nfiles)
		# calculate the standard deviation too
		reward_std = torch.sqrt(torch.sum(ro_reward_sum**2,1)/(bs*nfiles))
		ro_reward_sum = ro_reward_sum / \
			( reward_std.unsqueeze(1).expand(-1,bs*nfiles) + 1)
		for i in range(0):
			indx = torch.randint(0, bs*nfiles, (10,))
			plt.plot(reward_mean.numpy(), 'k')
			plt.plot(reward_std.numpy(), 'k-')
			plt.plot(ro_reward_sum[:,indx].numpy())
			plt.show()
			
		qfun = Gracoonizer(xfrmr_dim=xfrmr_dim, world_dim=world_dim, n_heads=4, n_layers=4).to(device) 
		qfun.printParamCount()
		optimizer = getOptimizer(optimizer_name, qfun)
		
		# get the locations of the board nodes. 
		_,reward_loc,locs = sparse_encoding.sudokuToNodes(torch.zeros(9,9),torch.zeros(9,9),torch.zeros(2),0,0,0.0)
		
		trainQfun(rollouts_board, ro_reward_sum, rollouts_action, 100000, memory_dict, model, qfun, hcoo, reward_loc, locs)
		
	
	uu = 0
	if cmd_args.t:
		while uu < NUM_ITERS:
			uu = train(args, memory_dict, model, train_dataloader, optimizer, hcoo, reward_loc, uu)
		
		# save after training
		model.save_checkpoint()
 
		# print("validation")
		validate(args, model, test_dataloader, optimizer_name, hcoo, uu)
