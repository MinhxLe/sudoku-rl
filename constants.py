n_heads = 3
world_dim = 1 + 9*3 + 8 # 36, must be even!
xfrmr_dim = 64 # default: 128
action_dim = 10 + 9 
latent_dim = xfrmr_dim - world_dim - action_dim
	# digits 0-9 (0=nothing); move, set/unset, note/unnote, nop
reward_dim = 2 # immediate and infinite-horizon
token_cnt = 96
latent_cnt = token_cnt - 82 # 14

batch_size = 32
