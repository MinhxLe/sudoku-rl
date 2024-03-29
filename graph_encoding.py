import math
import numpy as np
import torch
from enum import Enum
from sudoku_gen import Sudoku
import matplotlib.pyplot as plt
from constants import SuN, SuH, SuK
from type_file import Types, Axes, Action 
import pdb


class Node: 
	def __init__(self, typ, val):
		self.typ = typ
		# Payload. -1 for null, also holds Axes values.
		self.value = float(val)
		self.loc = 0
		self.kids = []
		self.parents = []
		
	def add_child(self, node): 
		# kid is type `Node
		self.kids.append(node)
		node.parents.append(self)
		
	def print(self, indent): 
		print(indent, self.typ.name, self.value)
		indent2 = indent + "  "
		for k in self.kids: 
			k.print(indent2)
			
	def count(self): 
		n = 1
		for k in self.kids: 
			n = n + k.count()
		return n

# seems like we need a bidirectional graph -- parents know about their children, and children about their parents, with some sort of asymmetric edges. 
# can allow for both undirected and directed links, guess. 
# let's start with a DAG for simplicity?
# how to encode a variable number of edges then? 

def sudokuActionNodes(action_type: int, action_val: int): 
	#TODO: Support multiple actions
	'''
	Returns a list containing action node: [action_node]

	action_node is a linear tree of size three
		action flag node -> axis node -> val 

	Input:
	action_val: (int) Represents either the magnitude+direction to travel along an axis (ex: +2, -2)
							or the digit corresponding to guess or set note
	'''
	na = Node(Types.ACTION, 0)
	if action_type == Action.LEFT.value or action_type == Action.RIGHT.value:
		ax = Axes.X_AX
	if action_type == Action.UP.value or action_type == Action.DOWN.value:
		ax = Axes.Y_AX
	

	nax = Node(Types.POSITION, ax)
	
	if action_type == Action.LEFT.value or action_type == Action.DOWN.value or\
		action_type == Action.RIGHT.value or action_type == Action.UP.value:
		naxx = Node(Types.LEAF, action_val)
	else: 
			raise ValueError(f'Unexpected non-movement action: {action_type}')
	
	na.add_child(nax)
	nax.add_child(naxx)
	return [na]

def sudokuToNodes(puzzle, curs_pos, action_type: int, action_val: int): 
	'''
	#TODO: Support multiple actions
	Returns a tuple of ([cursor_node], list of action_nodes)
		cursor_node is a tree which has two children- a node representing x position
		and a node representing y position. Each position node has a value child 

	Input:
	action_val: (int) Represents either the mag+direction to travel along an axis (ex: +2, -2)
							or the digit corresponding to guess or set note
	'''
	nodes = []
	
	nc = Node(Types.CURSOR, 0)
	posOffset = (SuN - 1) / 2.0

	ncx = Node(Types.POSITION, Axes.X_AX) # x = column
	ncxx = Node(Types.LEAF, curs_pos[0] - posOffset) # -4 -> 0 4 -> 8 
	ncx.add_child(ncxx)

	ncy = Node(Types.POSITION, Axes.Y_AX)
	ncyy = Node(Types.LEAF, curs_pos[1] - posOffset)
	ncy.add_child(ncyy)

	nc.add_child(ncx)
	nc.add_child(ncy)
	
	nodes.append(nc)
	
	actnodes = sudokuActionNodes(action_type, action_val)
	
	if False: 
		for y in range(SuN): 
			for x in range(SuN): 
				v = puzzle[y,x]
				nb = Node(Types.BOX, v)
				nbx = Node(Types.POSITION, Axes.X_AX)
				nbxx = Node(Types.LEAF, x - posOffset)
				nby = Node(Types.POSITION, Axes.Y_AX)
				nbyy = Node(Types.LEAF, y - posOffset)
				b = (y // SuH)*SuH + (x // SuH)
				nbb = Node(Types.POSITION, Axes.B_AX)
				nbbb = Node(Types.LEAF, b - posOffset)
				
				highlight = 0 # this is mostly icing..
				if x == curs_pos[0] and y == curs_pos[1]: 
					highlight = 1
				nbh = Node(Types.POSITION, Axes.H_AX)
				nbhh = Node(Types.LEAF, highlight)
				
				nbx.add_child(nbxx)
				nby.add_child(nbyy)
				nbb.add_child(nbbb)
				nbh.add_child(nbhh)
				nb.add_child(nbx)
				nb.add_child(nby)
				nb.add_child(nbb)
				nb.add_child(nbh)
				
				nodes.append(nb)
			
	if False: 
		print("total number of nodes:", sum([n.count() for n in nodes]))
		for n in nodes: 
			n.print("")
		
	return nodes, actnodes

# in the mask: 
# 1 = attend to self (so .. just project + nonlinearity)
# 2 = attend to children
# 4 = attend to parents
# 8 = attend to peers
# -- assume softmax is over columns.
def maskNode(node, msk):
	msk[node.loc, node.loc] = 1.0
	for kid in node.kids: 
		msk[kid.loc, node.loc] = 2.0
	for parent in node.parents: 
		msk[parent.loc, node.loc] = 4.0
	for kid in node.kids: 
		maskNode(kid, msk)	
	
def encodeNodes(bnodes, actnodes):
	'''
	Given board nodes and action nodes, returns a board encoding which encodes every board node,
		action encoding which encodes every action node, and a mask based on board and action nodes
	The board and action nodes have the same encoding- contains one hot of node type and node value

	Returns:
	benc: Shape (#board nodes x 20)
	actenc: Shape (#action nodes x 20)
	msk: Shape (#board+action nodes x #board+action)  
	'''

	bcnt = sum([n.count() for n in bnodes])
	actcnt = sum([n.count() for n in actnodes])
	benc = np.zeros((bcnt, 20), dtype=np.float32)
	actenc = np.zeros((actcnt, 20), dtype=np.float32)
	
	def encode_node(i, m, node, encoding): 
		'''
		Recursive function which populates the encoding matrix.
		Each encoded vector of the node (a row) contains a one-hot encoding of the node type
			(i.e cursor, position, leaf, box, action) and also contains the node value
		The recursion is such that the order is DFS 
		'''
		encoding[i, node.typ.value] = 1.0 # categorical
		# enc[i, node.value + 10] = 1.0 # categorical
		encoding[i, 10] = node.value # ordinal
		node.loc = m # save loc for mask.
		i = i + 1
		m = m + 1
		for k in node.kids: 
			i,m = encode_node(i, m, k, encoding)
		return i,m
			
	i = 0
	m = 0
	for n in bnodes: 
		i,m = encode_node(i, m, n, benc)
	i = 0
	for n in actnodes: 
		i,m = encode_node(i, m, n, actenc)
	
	nodes = bnodes + actnodes
	cnt = bcnt + actcnt
	msk = np.zeros((cnt,cnt), dtype=np.float32)
	
	for n in nodes: 
		maskNode(n, msk)
	# let all top-level nodes communicate. 
	for n in nodes: 
		for m in nodes: 
			if n != m: 
				msk[n.loc, m.loc] = 8.0
	
	return benc, actenc, msk

def test_nodes(): 
	na = Node(Types.BOX, 0)
	naa = Node(Types.LEAF, 1)
	nab = Node(Types.LEAF, 2)
	nb = Node(Types.BOX, 3)
	nba = Node(Types.POSITION, 4)
	nbaa = Node(Types.LEAF, 5)
	nbb = Node(Types.POSITION, 6)
	nbba = Node(Types.LEAF, 7)
	
	na.add_child(naa)
	na.add_child(nab)
	nb.add_child(nba)
	nb.add_child(nbb)
	nba.add_child(nbaa)
	nbb.add_child(nbba)
	
	nodes = [na, nb]
	return nodes

if __name__ == "__main__":
	plot_rows = 1
	plot_cols = 2
	figsize = (16, 8)
	# plt.ion()
	fig, axs = plt.subplots(plot_rows, plot_cols, figsize=figsize)
	im = [0,0]
	
	N = SuN
	K = SuK
	sudoku = Sudoku(N, K)
	sudoku.fillValues()
	sudoku.printSudoku()
	
# 	nodes = test_nodes()
# 	enc,aenc,msk = encodeNodes(nodes, [])
# 	
# 	im[0] = axs[0].imshow(enc.T)
# 	plt.colorbar(im[0], ax=axs[0])
# 	im[1] = axs[1].imshow(msk)
# 	plt.colorbar(im[1], ax=axs[1])
# 	plt.show()
	
	nodes, actnodes = sudokuToNodes(sudoku.mat, np.ones((2,))*2.0, 0)
	
	benc,actenc,msk = encodeNodes(nodes, actnodes)
	enc = np.concatenate((benc, actenc), axis=0)
	print(enc.shape, actenc.shape, msk.shape)
	im[0] = axs[0].imshow(enc.T)
	plt.colorbar(im[0], ax=axs[0])
	im[1] = axs[1].imshow(msk)
	plt.colorbar(im[1], ax=axs[1])
	plt.show()

