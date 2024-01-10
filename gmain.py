import math
import random
import numpy as np
import torch
from torch import nn, optim
import pdb
import matplotlib.pyplot as plt
import graph_encoding
from graph_model import Gracoonizer
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


def runAction(action, sudoku, cursPos): 
	# run the action, update the world, return the reward.
	act = action # TODO decode arguments later.
	# act = b % 4
	reward = -0.05
	if act == 0: # up
		cursPos[0] -= 1
	if act == 1: # right
		cursPos[1] += 1
	if act == 2: # down
		cursPos[0] += 1
	if act == 3: # left
		cursPos[1] -= 1
	cursPos[0] = cursPos[0] % 9 # wrap at the edges; 
	cursPos[1] = cursPos[1] % 9 # works for negative nums
			
	if True: 
		sact = actionName(act)
		print(f'runAction @ {cursPos[0]},{cursPos[1]}: {sact}')
	
	return reward


def encodeBoard(sudoku, cursPos, action): 
	nodes = graph_encoding.sudoku_to_nodes(sudoku.mat, cursPos, action)
	enc,msk = graph_encoding.encode_nodes(nodes)
	
	reward = runAction(action, sudoku, cursPos)
	
	nodes = graph_encoding.sudoku_to_nodes(sudoku.mat, cursPos, action)
	enc_new,_ = graph_encoding.encode_nodes(nodes)
	
	return enc, msk, enc_new, reward


def enumerateMoves(depth, episode): 
	# moves = range(8)
	moves = range(4) # only move! 
	outlist = []
	if depth > 0: 
		for m in moves:
			outlist.append(episode + [m])
			outlist = outlist + enumerateMoves(depth-1, episode + [m])
	return outlist


def enumerateBoards(puzzles, n): 
	lst = enumerateMoves(1, [])
	if len(lst) < n: 
		rep = n // len(lst) + 1
		lst = lst * rep
	if len(lst) > n: 
		lst = random.sample(lst, n)
	sudoku = Sudoku(9, 25)
	boards = [] # collapse to one tensor afterward. 
	rewards = torch.zeros(n)
	for i, ep in enumerate(lst): 
		puzzl = puzzles[i, :, :]
		sudoku.setMat(puzzl.numpy())
		cursPos = torch.randint(9, (2,))
		enc,msk,enc_new,reward = encodeBoard(sudoku, cursPos, ep[0])
		boards.append(torch.tensor(enc))
		boards.append(torch.tensor(enc_new))
		rewards[i] = reward
		
	board_enc = torch.stack(boards) # note! alternating old and new. 
	board_msk = torch.tensor(msk)
	return board_enc,board_msk,rewards


if __name__ == '__main__':
	puzzles = torch.load('puzzles_500000.pt')
	n = 10000
	device = torch.device(type='cuda', index=1)
	
	try: 
		fname = f'board_enc_{n}.pt'
		board_enc = torch.load(fname)
		print(f'loaded {fname}')
		# wait ... the graph never changes, just the data. 
		# only need one mask!
		fname = f'board_msk_{n}.pt'
		board_msk = torch.load(fname)
		print(f'loaded {fname}')
		fname = f'board_reward_{n}.pt'
		board_reward = torch.load(fname)
		print(f'loaded {fname}')
	except: 
		board_enc,board_msk,board_reward = enumerateBoards(puzzles, n)
		fname = f'board_enc_{n}.pt'
		torch.save(board_enc, fname)
		fname = f'board_msk_{n}.pt'
		torch.save(board_msk, fname)
		fname = f'board_reward_{n}.pt'
		torch.save(board_reward, fname)
	print(board_enc.shape, board_msk.shape, board_reward.shape)
	
	fd_board = make_mmf("board.mmap", [batch_size, token_cnt, world_dim])
	fd_new_board = make_mmf("new_board.mmap", [batch_size, token_cnt, world_dim])
	fd_boardp = make_mmf("boardp.mmap", [batch_size, token_cnt, world_dim])
	fd_reward = make_mmf("reward.mmap", [batch_size, reward_dim])
	fd_rewardp = make_mmf("rewardp.mmap", [batch_size, reward_dim])
	fd_attention = make_mmf("attention.mmap", [2, token_cnt, token_cnt, n_heads])
	fd_wqkv = make_mmf("wqkv.mmap", [n_heads*2,2*xfrmr_dim,xfrmr_dim])

	fd_losslog = open('losslog.txt', 'w')
	
	# need to repack the mask to a sparse tensor w/ 3 duplicates. 
	msk = torch.zeros((board_msk.shape[0], board_msk.shape[1], 12), dtype=torch.int8) # try to save memory...
	for i in range(12): 
		j = i % 4
		msk[:, :, i] = ( board_msk == (2**j) )
	msk = msk.unsqueeze(0).expand([batch_size, -1, -1, -1])
	# msk = msk.to_sparse() # idk if you can have views of sparse tensors.. ??
	# sparse tensors don't work with einsum, alas.
	msk = msk.to(device)
	
	model = Gracoonizer(xfrmr_dim = 20, world_dim = 20, reward_dim = 1).to(device)
	model.printParamCount()
	
	optimizer = optim.AdamW(model.parameters(), lr=2e-4, weight_decay = 1e-2)
	
	for u in range(100000): 
		i = torch.randint(n, (batch_size,)) * 2
		x = board_enc[i, :, :].to(device)
		y = board_enc[i+1,:,:].to(device)
		reward = board_reward[i//2]
		yp,rp,a1,a2,w1,w2 = model.forward(x, msk, u)
		
		loss = torch.sum((yp - y)**2)
		loss.backward()
		optimizer.step() 
		print(loss.cpu().item())
		fd_losslog.write(f'{u}\t{loss.cpu().item()}\n')
		fd_losslog.flush()
		
		if u % 24 == 0: 
			write_mmap(fd_board, x.cpu())
			write_mmap(fd_new_board, y.cpu())
			write_mmap(fd_boardp, yp.cpu().detach())
			write_mmap(fd_reward, reward.cpu())
			write_mmap(fd_rewardp, rp.cpu().detach())
			write_mmap(fd_attention, torch.stack((a1, a2), 0))
			write_mmap(fd_wqkv, torch.stack((w1, w2), 0))
			
