import sys

import argparse
import torch
from rt.dataset import SudokuSATNetDataset
from rt.utils import set_seed
from rt.model import GPT, GPTConfig
from rt.trainer import Trainer, TrainerConfig
from rt.network import testNN
# from helper import print_result, visualize_adjacency


def main(args):
    print("Hyperparameters: ", args.hyper)

    # generate the name of this experiment
    prefix = f"[{args.dataset},{args.n_train//1000}k]"
    if args.loss:
        prefix += (
            "["
            + "-".join(args.loss)
            + ";"
            + "-".join([str(v) for v in args.hyper])
            + "]"
        )
    prefix += f"L{args.n_layer}R{args.n_recur}H{args.n_head}_"


    #############
    # Seed everything for reproductivity
    #############
    set_seed(args.seed)

    #############
    # Load data
    #############
    train_dataset_ulb = None
    if args.dataset == "satnet":
        dataset = SudokuSATNetDataset()
        indices = list(range(len(dataset)))
        # we use the same test set in the SATNet repository for comparison
        test_dataset = torch.utils.data.Subset(dataset, indices[-1000:])
        train_dataset = torch.utils.data.Subset(
            dataset, indices[: min(9000, args.n_train)]
        )
    else:
        raise NotImplementedError
    print(
        f"[{args.dataset}] use {len(train_dataset)} for training and {len(test_dataset)} for testing"
    )

    #############
    # Construct a GPT model and a trainer
    #############
    # vocab_size is the number of different digits in the input
    mconf = GPTConfig(
        vocab_size=10,
        block_size=81,
        n_layer=args.n_layer,
        n_head=args.n_head,
        n_embd=args.n_embd,
        num_classes=9,
        causal_mask=False,
        losses=args.loss,
        n_recur=args.n_recur,
        all_layers=args.all_layers,
        hyper=args.hyper,
    )
    model = GPT(mconf)

    tconf = TrainerConfig(
        max_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        lr_decay=args.lr_decay,
        warmup_tokens=1024,  # until which point we increase lr from 0 to lr; lr decays after this point
        final_tokens=100 * len(train_dataset),  # at what point we reach 10% of lr
        eval_funcs=[testNN],  # test without inference trick
        eval_interval=args.eval_interval,  # test for every eval_interval number of epochs
        gpu=args.gpu,
        prefix=prefix,
    )

    trainer = Trainer(model, train_dataset, test_dataset, tconf)

    #############
    # Start training
    #############
    trainer.train()
    result = trainer.result
    print(f"Total and single accuracy are the board and cell accuracy respectively. {result}")
    #print_result(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Training
    parser.add_argument("--epochs", type=int, default=200, help="Number of epochs.")
    parser.add_argument(
        "--eval_interval",
        type=int,
        default=1,
        help="Compute accuracy for how many number of epochs.",
    )
    parser.add_argument("--batch_size", type=int, default=16, help="Batch size")
    parser.add_argument("--lr", type=float, default=6e-4, help="Learning rate")
    parser.add_argument(
        "--lr_decay",
        default=False,
        action="store_true",
        help="use lr_decay defined in minGPT",
    )
    # Model and loss
    parser.add_argument(
        "--n_layer",
        type=int,
        default=1,
        help="Number of sequential self-attention blocks.",
    )
    parser.add_argument(
        "--n_recur",
        type=int,
        default=32,
        help="Number of recurrency of all self-attention blocks.",
    )
    parser.add_argument(
        "--n_head",
        type=int,
        default=4,
        help="Number of heads in each self-attention block.",
    )
    parser.add_argument(
        "--n_embd", type=int, default=128, help="Vector embedding size."
    )
    parser.add_argument(
        "--loss", default=[], nargs="+", help="specify regularizers in \{c1, att_c1\}"
    )
    parser.add_argument(
        "--all_layers",
        default=False,
        action="store_true",
        help="apply losses to all self-attention layers",
    )
    parser.add_argument(
        "--hyper",
        default=[1, 0.1],
        nargs="+",
        type=float,
        help="Hyper parameters: Weights of [L_sudoku, L_attention]",
    )
    # Data
    parser.add_argument(
        "--dataset",
        type=str,
        default="satnet",
        help="Name of dataset in \{satnet, palm, 70k\}",
    )
    parser.add_argument(
        "--n_train", type=int, default=9000, help="The number of data for training"
    )
    parser.add_argument(
        "--n_test", type=int, default=1000, help="The number of data for testing"
    )
    # Other
    parser.add_argument(
        "--seed", type=int, default=0, help="Random seed for reproductivity."
    )
    parser.add_argument(
        "--gpu",
        type=int,
        default=-1,
        help="gpu index; -1 means using all GPUs or using CPU if no GPU is available",
    )
    parser.add_argument(
        "--debug", default=False, action="store_true", help="debug mode"
    )

    parser.add_argument(
        "--comment", type=str, default="", help="Comment of the experiment"
    )
    args = parser.parse_args()
    main(args)
