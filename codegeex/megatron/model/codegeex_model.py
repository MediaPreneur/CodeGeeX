# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch
from codegeex.megatron import get_args
from codegeex.megatron import mpu
from .module import MegatronModule

from .language_model import parallel_lm_logits
from .language_model import get_language_model
from .utils import init_method_normal
from .utils import scaled_init_method_normal


class CodeGeeXModel(MegatronModule):
    """Code Generative Model for Multilingual Program Synthesis."""

    def __init__(self, num_tokentypes=0, parallel_output=False):
        super(CodeGeeXModel, self).__init__()
        args = get_args()

        self.parallel_output = parallel_output
        self.fp16_lm_cross_entropy = args.fp16_lm_cross_entropy

        self.language_model, self._language_model_key = get_language_model(
            num_tokentypes=num_tokentypes,
            add_pooler=False,
            init_method=init_method_normal(args.init_method_std),
            scaled_init_method=scaled_init_method_normal(args.init_method_std,
                                                         args.num_layers))

    def forward(
            self,
            input_ids,
            position_ids,
            attention_mask,
            labels=None,
            tokentype_ids=None,
            layer_past=None,
            get_key_value=False,
            forward_method_parallel_output=None,
            prompt_length=None,
            context_length=None,
    ):

        # Language model.
        lm_output = self.language_model(input_ids,
                                        position_ids,
                                        attention_mask,
                                        tokentype_ids=tokentype_ids,
                                        layer_past=layer_past,
                                        get_key_value=get_key_value,
                                        prompt_length=prompt_length,
                                        context_length=context_length)

        if get_key_value:
            lm_output, presents = lm_output

        lm_output = torch.add(lm_output, 0)
        # Output.
        parallel_output = self.parallel_output
        if forward_method_parallel_output is not None:
            parallel_output = forward_method_parallel_output
        output = parallel_lm_logits(
            lm_output,
            self.language_model.embedding.word_embeddings.weight,
            parallel_output)

        if get_key_value:
            output = [output, presents]

        if labels is None:
            return output
        if not self.fp16_lm_cross_entropy:
            return mpu.vocab_parallel_cross_entropy(output.float(), labels)

        assert output.dtype == torch.half
        return mpu.vocab_parallel_cross_entropy(output, labels)

    def state_dict_for_save_checkpoint(self, destination=None, prefix='',
                                       keep_vars=False):

        return {
            self._language_model_key: self.language_model.state_dict_for_save_checkpoint(
                destination, prefix, keep_vars
            )
        }

    def load_state_dict(self, state_dict, strict=True):
        """Customized load."""

        if self._language_model_key in state_dict:
            state_dict = state_dict[self._language_model_key]
        self.language_model.load_state_dict(state_dict, strict=strict)
