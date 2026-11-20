# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Domain vocabulary of the negotiation content model (port of the Java ``content/Vocabulary``).

The vocabulary is the single source of the section titles, slot names, appended line labels and
list punctuation used when rendering negotiation messages and when recognising sections inside
received messages. The canonical keys are language-neutral; every supported language exposes
exactly the same key set, while the values match the bundled template bytes of that language
verbatim.

The Java class loads the vocabulary directly from the classpath (negotiation vocabularies are
classpath-fixed and never configurable). This port routes it through the common resource access
layer instead (D13 as overridden by D31): :meth:`Vocabulary.for_language` consumes the layer's
vocabulary loader — packaged by default, locally overridable in ``local_file`` mode — and owns no
IO, caching or path assembly of its own. The strict 37-key contract (a vocabulary that does not
exist, cannot be read, is malformed, or drifts from the canonical key set fails fast with a
catalog-coded ``infra.resource_read_failed`` error) is likewise delegated to the common layer.

Each resolved vocabulary is cached per access object and language, mirroring the Java
resolve-once-per-JVM-and-classloader assembly-time snapshot: an access object's view is frozen for
its lifetime, and a restarted access (the Python equivalent of a restart) re-resolves. The bundled
resource is the source of truth for the key set: 37 canonical keys (the port plan's "41 keys" was
a miscount carried from the pre-port gap analysis).
"""

from __future__ import annotations

from typing import Final
from weakref import WeakKeyDictionary

from a2a_t.common.prompt_resources.resource_access import (
    PackagedPromptResourceAccess,
    PromptResourceAccess,
)
from a2a_t.common.prompt_resources.vocabulary import (
    CANONICAL_KEYS as _COMMON_CANONICAL_KEYS,
)
from a2a_t.common.prompt_resources.vocabulary import Vocabulary as _CommonVocabulary

__all__ = ["CANONICAL_KEYS", "Vocabulary"]

#: The language-neutral canonical key set every vocabulary file must define exactly, in the fixed
#: order pinned by the Java ``Vocabulary.CANONICAL_KEYS`` list (delegated to the common layer).
CANONICAL_KEYS: Final[tuple[str, ...]] = _COMMON_CANONICAL_KEYS

#: Frozen per-access vocabulary cache: one resolved vocabulary per (access, language) pair.
_CACHE: Final[WeakKeyDictionary[PromptResourceAccess, dict[str, Vocabulary]]] = WeakKeyDictionary()

_DEFAULT_ACCESS: PackagedPromptResourceAccess | None = None


class Vocabulary(_CommonVocabulary):
    """Language-specific text constants for negotiation templates.

    Attributes:
        language: locale identifier this vocabulary is bound to, such as ``zh-CN``.
        entries: canonical key to language-specific text mapping; treat it as immutable.
    """

    @classmethod
    def for_language(cls, language: str, access: PromptResourceAccess | None = None) -> Vocabulary:
        """Return the vocabulary for one language, resolved through the common access layer.

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.
            access: resource access object resolving the vocabulary tree; ``None`` uses the
                packaged access (the Java classpath-fixed default).

        Returns:
            the vocabulary holding the validated text constants of that language, cached per
            access object and language.

        Raises:
            ValueError: when the language is not a non-blank simple path segment.
            A2ATError: carrying ``infra.resource_read_failed`` when the vocabulary does not exist,
                cannot be read, is malformed or does not define exactly the canonical key set.
        """
        resolved = _default_access() if access is None else access
        per_access = _CACHE.setdefault(resolved, {})
        vocabulary = per_access.get(language)
        if vocabulary is None:
            loaded = resolved.load_vocabulary(language)
            vocabulary = cls(language=loaded.language, entries=loaded.entries)
            per_access[language] = vocabulary
        return vocabulary


def _default_access() -> PackagedPromptResourceAccess:
    """Return the module-level packaged access used when no access object is injected."""
    global _DEFAULT_ACCESS
    if _DEFAULT_ACCESS is None:
        _DEFAULT_ACCESS = PackagedPromptResourceAccess()
    return _DEFAULT_ACCESS
