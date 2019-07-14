import torch
import torch.nn as nn
import numpy as np
import os
import time
from copy import deepcopy
from collections import deque
from .network import *
from .replay import *
from .utils import *


class SACAgent:

    def __init__(self, env, qnet, vnet, actornet, summary_writer, start_steps=10000, train_after_steps=1,
                 gradient_steps=1, gradient_clip=1, gamma=0.99, minibatch_size=256, buffer_size=10e5,
                 polyak=0.001, max_eps_len=10e4, temperature=0.1):

        super().__init__()
        self.env = env
        self.states = env.reset()
        self.summary_writer = summary_writer
        self.qnet = [qnet, deepcopy(qnet)]
        self.qnet[1].apply(layer_init)
        self.vnet = vnet
        self.vnet_target = deepcopy(vnet)
        self.actornet = actornet
        self.start_steps = start_steps
        self.train_after_steps = train_after_steps
        self.gradient_steps = gradient_steps
        self.gamma = gamma
        self.gradient_clip = gradient_clip
        self.minibatch_size = minibatch_size
        self.replay_buffer = Replay(buffer_size, minibatch_size)
        self.polyak = polyak
        self.max_eps_len = max_eps_len
        self.temperature = temperature

        self.qnet_opt = [torch.optim.Adam(q.parameters()) for q in self.qnet]
        self.vnet_opt = torch.optim.Adam(self.vnet.parameters())
        self.actornet_opt = torch.optim.Adam(self.actornet.parameters())

        self.step_counter = 0

    def train_step(self):
        if len(self.replay_buffer) < self.start_steps:
            for _ in range(self.start_steps):
                actions = [self.env.action_space.sample() for _ in range(self.env.num_envs)]
                next_states, rewards, dones, info = self.env.step(actions)
                self.replay_buffer.add_vec([self.states, actions, rewards, next_states, dones])
                self.states = next_states
            print(f"Replay buffer initialized with {len(self.replay_buffer)} random steps")

        # self.actornet.eval()
        with torch.no_grad():
            actions, log_prob, _ = self.actornet.sample(self.states)
        # self.actornet.train()
        actions = actions.cpu().detach().numpy()
        next_states, rewards, dones, info = self.env.step(actions)
        self.replay_buffer.add_vec([self.states, actions, rewards, next_states, dones])
        self.states = next_states
        self.step_counter += 1
        # print("Updating model")
        if self.step_counter % self.train_after_steps == 0:
            for _ in range(self.gradient_steps):
                self.update_models()
        return rewards, dones

    def update_models(self):

        states, actions, rewards, next_states, dones = self.replay_buffer.sample()

        with torch.no_grad():
            val_next_state = self.vnet_target(next_states)
            q_target = rewards + (1 - dones) * val_next_state.cpu().detach().squeeze(-1).numpy()
            q_target = tensor(q_target).to(DEVICE).unsqueeze(-1)
            actions_v, log_pi_v, _ = self.actornet.sample(states)
            qvals_v = [qf(states, actions_v) for qf in self.qnet]
            value_target = torch.min(*qvals_v) - self.temperature * log_pi_v

        qvals = [qf(states, actions) for qf in self.qnet]
        mse = nn.MSELoss()
        qloss = [mse(qv, q_target) for qv in qvals]
        value_loss = mse(self.vnet(states), value_target)

        actions_p, log_pi_p, _ = self.actornet.sample(states)
        qval_p = torch.min(*[qf(states, actions_p) for qf in self.qnet])
        actor_loss = ((self.temperature * log_pi_p) - qval_p).mean()


        for i in range(2):
            self.qnet_opt[i].zero_grad()
            qloss[i].backward()
            torch.nn.utils.clip_grad_norm_(self.qnet[i].parameters(), self.gradient_clip)
            self.qnet_opt[i].step()

        self.vnet_opt.zero_grad()
        value_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.vnet.parameters(), self.gradient_clip)
        self.vnet_opt.step()

        self.actornet_opt.zero_grad()
        actor_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.actornet.parameters(), self.gradient_clip)
        self.actornet_opt.step()

        soft_update(self.vnet_target, self.vnet, self.polyak)

    def learn(self, iterations=1e5):

        eps_rewards = deque(maxlen=100)
        eps_rewards.append(0)
        running_rewards = np.zeros(self.env.num_envs)
        start_time = time.time()
        for i in range(iterations):
            rewards, dones = self.train_step()
            running_rewards += rewards
            for idx, done in enumerate(dones):
                if done:
                    eps_rewards.append(running_rewards[idx])
                    running_rewards[idx] = 0
            if i % (iterations // 1000) == 0:
                fps = (iterations // 1000) // (time.time() - start_time)
                start_time = time.time()
                print("Steps: {:8d}\tfps: {:4f}\tLastest Episode reward: {:4f}\tMean Rewards: {:4f}".format(
                    i, fps, eps_rewards[-1], np.mean(eps_rewards)
                ), end='\r')

    def eval_step(self):
        pass

    def eval(self, gif_path=None, num_episodes=1):
        pass

    def save_model(self, dirpath='.'):
        if not os.path.exists(dirpath):
            raise Exception("Path does not exist")
        print(f"Saving models in directory {dirpath}")
        torch.save(self.actornet.state_dict(), os.path.join(dirpath, 'actor.pt'))
        torch.save(self.qnet[0].state_dict(), os.path.join(dirpath, 'qnet0.pt'))
        torch.save(self.qnet[1].state_dict(), os.path.join(dirpath, 'qnet1.pt'))
        torch.save(self.vnet.state_dict(), os.path.join(dirpath, 'vnet.pt'))

    def load_model(self, dirpath='.'):
        if not os.path.exists(dirpath):
            raise Exception("Path does not exist")
        print(f"Loading models from directory {dirpath}")
        self.actornet.load_state_dict(torch.load(os.path.join(dirpath, 'actor.pt')))
        self.qnet[0].load_state_dict(torch.load(os.path.join(dirpath, 'qnet0.pt')))
        self.qnet[1].load_state_dict(torch.load(os.path.join(dirpath, 'qnet1.pt')))
        self.vnet.load_state_dict(torch.load(os.path.join(dirpath, 'vnet.pt')))