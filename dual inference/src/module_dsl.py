import random
import math
import time

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from utils import *


class DSLCriterion(nn.Module):
    def __init__(self, loss_weight, pretrain_epochs=0,
                 LM=None, MADE=None, lambda_xy=0.1, lambda_yx=0.1,
                 made_n_samples=1, propagate_other=False, with_intent=True):
        super(DSLCriterion, self).__init__()
        self.pretrain_epochs = pretrain_epochs
        self.epoch = 0
        self.propagate_other = propagate_other
        self.lambda_xy = lambda_xy
        self.lambda_yx = lambda_yx
        self.with_intent = with_intent
        self.LM = LM
        self.MADE = MADE
        if LM is None:
            raise ValueError("Language model not provided")
        if MADE is None:
            raise ValueError("MADE model not provided")

        self.made_n_samples = made_n_samples
        # self.BCE = nn.BCEWithLogitsLoss(reduction='sum')
        self.CE = nn.CrossEntropyLoss(weight=loss_weight, reduction='sum', ignore_index=-1)
        self.NLUCE = nn.CrossEntropyLoss(reduction='sum', ignore_index=-1)

    def get_log_joint_prob_nlg(self, logits, decisions):
        """
        args:
            logits: tensor of shape [batch_size, seq_length, vocab_size]
            decisions: tensor of shape [batch_size, seq_length, vocab_size]
                       one-hot vector of decoded word-ids
        returns:
            log_joint_prob: tensor of shape [batch_size]
        """
        probs = torch.softmax(logits, dim=-1)
        return (decisions * probs).sum(dim=-1).log().sum(dim=-1)

    def get_log_joint_prob_nlu(self, logits, decisions):
        """
        args:
            logits: tensor of shape [batch_size, attr_vocab_size]
            decisions: tensor of shape [batch_size, attr_vocab_size]
                       decisions(0/1)
        returns:
            log_joint_prob: tensor of shape [batch_size]
        """
        probs = torch.softmax(logits, dim=-1).squeeze()
        # pdb.set_trace()
        mask = decisions > 0
        try:
            ret = torch.gather(probs, dim=-1, index=decisions.clamp(0, 100000).cpu().long().unsqueeze(-1))
            ret = ret.squeeze() * mask.float()
        except:
            pdb.set_trace()
        return ret.sum(dim=-1).log()
        # return (decisions * probs).sum(dim=-1).log().sum(dim=-1)
    
    def get_log_joint_prob_intent(self, logits, decisions):
        """
        args:
            logits: tensor of shape [batch_size, attr_vocab_size]
            decisions: tensor of shape [batch_size, attr_vocab_size]
                       decisions(0/1)
        returns:
            log_joint_prob: tensor of shape [batch_size]
        """
        probs = torch.softmax(logits, dim=-1).squeeze()
        if not self.with_intent:
            return probs.log()
        # pdb.set_trace()
        # mask = decisions > 0
        try:
            ret = torch.gather(probs, dim=-1, index=decisions.cpu().long().unsqueeze(-1))
            # ret = ret.squeeze() * mask.float()
        except:
            pdb.set_trace()
        return ret.log()
        # return (decisions * probs).sum(dim=-1).log().sum(dim=-1)

    def epoch_end(self):
        self.epoch += 1
        if self.epoch == self.pretrain_epochs:
            print_time_info("pretrain finished, starting using duality loss")

    def get_scheduled_loss(self, dual_loss):
        if self.epoch < self.pretrain_epochs:
            return torch.tensor(0.0)
        return dual_loss

    def forward(self, nlg_logits, nlg_outputs, nlg_targets,
                nlu_logits, nlu_targets, 
                intent_logits, intent_targets, attrs):
        """
        args:
            nlg_logits: tensor of shape [batch_size, seq_length, vocab_size]
            nlg_outputs: tensor of shape [batch_size, seq_length, vocab_size]
            nlg_targets: tensor of shape [batch_size, seq_length]
            nlu_logits: tensor of shape [batch_size, attr_vocab_size]
            nlu_targets: tensor of shape [batch_size, attr_vocab_size]
        """
        # TODO
        nlg_logits_1d = nlg_logits.contiguous().view(-1, nlg_logits.size(-1))
        nlg_targets_1d = nlg_targets.contiguous().view(-1)
        nlu_logits_1d = nlu_logits.contiguous().view(-1, nlu_logits.size(-1))
        nlu_targets_1d = nlu_targets.contiguous().view(-1)
        nlg_sup_loss = self.CE(nlg_logits_1d, nlg_targets_1d)
        nlu_sup_loss = self.NLUCE(nlu_logits_1d, nlu_targets_1d) \
            + self.NLUCE(intent_logits, intent_targets)
        # pdb.set_trace()

        log_p_x = self.LM.get_log_prob(nlg_targets)
        log_p_y = self.MADE.get_log_prob(attrs, n_samples=self.made_n_samples)

        log_p_y_x = self.get_log_joint_prob_nlg(nlg_logits, nlg_outputs)
        # nlu_decisions = (nlu_logits.sigmoid() >= 0.5).float()
        try:
            log_p_x_y = self.get_log_joint_prob_nlu(nlu_logits, nlu_targets) \
            * self.get_log_joint_prob_intent(intent_logits, intent_targets)
        except:
            pdb.set_trace()

        try:
            if self.propagate_other:
                nlg_loss_dual = (log_p_x + log_p_y_x - log_p_y - log_p_x_y).pow(2).mean()
                nlu_loss_dual = (log_p_x + log_p_y_x - log_p_y - log_p_x_y).pow(2).mean()
            else:
                nlg_loss_dual = (log_p_x + log_p_y_x - log_p_y - log_p_x_y.detach()).pow(2).mean()
                nlu_loss_dual = (log_p_x + log_p_y_x.detach() - log_p_y - log_p_x_y).pow(2).mean()
        except:
            pdb.set_trace()

        nlg_loss_dual = self.lambda_xy * self.get_scheduled_loss(nlg_loss_dual)
        nlu_loss_dual = self.lambda_yx * self.get_scheduled_loss(nlu_loss_dual)
        # pdb.set_trace()
        return nlg_sup_loss + nlg_loss_dual, nlu_sup_loss + nlu_loss_dual, nlg_loss_dual
