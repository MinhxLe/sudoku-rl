import numpy as np
import csv
import pdb
import matplotlib.pyplot as plt
import time
import os
import argparse
# import sklearn

# remove menubar buttons
# plt.rcParams['toolbar'] = 'None'

plot_rows = 1
plot_cols = 1
figsize = (7 	, 4)
plt.ion()
fig, ax = plt.subplots(plot_rows, plot_cols, figsize=figsize)
initialized = False

current_directory = os.getcwd()
base_dir = os.path.basename(current_directory)
fig.canvas.manager.set_window_title(f'plot_losslog {base_dir}')

parser = argparse.ArgumentParser(description="Plot a txt file of losses")
parser.add_argument('-f', type=str, default="./losslog.txt", help='which file to read')
parser.add_argument('-f2', type=str, default="", help='comparison file')
cmd_args = parser.parse_args()

def slidingWindowR2(x, y, window_size, stride):
	n = len(x)
	r2_values = []

	for i in range(0, n - window_size + 1, stride):
		x_window = x[i : i + window_size]
		y_window = y[i : i + window_size]
		r2 = np.corrcoef(x_window, y_window)
		r2_values.append(np.clip(r2[0,1], -1, 1))

	return r2_values


def isFileEmpty(file_path):
    # Check if file exist and it is empty
    return os.path.exists(file_path) and os.path.getsize(file_path) == 0


while True: 
	fname = cmd_args.f
	fname2 = cmd_args.f2
	with open(fname, 'r') as x:
		data = list(csv.reader(x, delimiter="\t"))
	data = np.array(data)
	data = data.astype(float)
	if fname2 != "":
		with open(fname2, 'r') as x:
			data2 = list(csv.reader(x, delimiter="\t"))
		data2 = np.array(data2)
		data2 = data2.astype(float)
	else:
		data2 = np.zeros((1,))

	ax.cla()
	if len(data.shape) > 1 and data.shape[0] > 1:
		ax.plot(data[:,0], np.log(data[:, 1]), 'b')
	if len(data2.shape) > 1 and data2.shape[0] > 1:
		ax.plot(data2[:,0], np.log(data2[:, 1]), 'k', alpha=0.5)
	ax.set(xlabel='iteration / batch #')
	ax.set_title('log loss')

	fig.tight_layout()
	fig.canvas.draw()
	fig.canvas.flush_events()
	time.sleep(2)
	print("tock")
