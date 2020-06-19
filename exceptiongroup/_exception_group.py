from typing import Optional, Tuple, Union, Type, Dict, Any, ClassVar, Sequence
from weakref import WeakValueDictionary


class ExceptionGroupMeta(type):
    """
    Metaclass to specialize :py:exc:`ExceptionGroup` for specific child types

    Provides specialization via subscription and corresponding type checks:
    ``Class[spec]`` and ``issubclass(Class[spec], Class[spec, spec2])``. Accepts
    the specialization ``...`` (a :py:const:`Ellipsis`) to mark the specialization
    as inclusive, meaning a subtype may have additional specializations.
    """

    # metaclass instance fields - i.e. class fields
    #: the base case, i.e. Class
    base_case: "ExceptionGroupMeta"
    #: whether additional child exceptions are allowed in issubclass checking
    inclusive: bool
    #: the specialization of some class - e.g. (TypeError,) for Class[TypeError]
    #: or None for the base case
    specializations: Optional[Tuple[Type[BaseException], ...]]
    #: internal cache for currently used specializations, i.e. mapping spec: Class[spec]
    _specs_cache: WeakValueDictionary

    def __new__(
        mcs,
        name: str,
        bases: Tuple[Type, ...],
        namespace: Dict[str, Any],
        specializations: Optional[Tuple[Type[BaseException], ...]] = None,
        inclusive: bool = True,
        **kwargs,
    ):
        cls = super().__new__(
            mcs, name, bases, namespace, **kwargs
        )  # type: ExceptionGroupMeta
        if specializations is not None:
            base_case = bases[0]
        else:
            inclusive = True
            base_case = cls
        cls.inclusive = inclusive
        cls.specializations = specializations
        cls.base_case = base_case
        return cls

    # Implementation Note:
    # The Python language translates the except clause of
    #   try: raise a
    #   except b as err: <block>
    # to ``if issubclass(type(a), b): <block>``.
    #
    # Which means we need just ``__subclasscheck__`` for error handling.
    # We implement ``__instancecheck__`` for consistency only.
    def __instancecheck__(cls, instance):
        """``isinstance(instance, cls)``"""
        return cls.__subclasscheck__(type(instance))

    def __subclasscheck__(cls, subclass):
        """``issubclass(subclass, cls)``"""
        # issubclass(EG, EG)
        if cls is subclass:
            return True
        try:
            base_case = subclass.base_case
        except AttributeError:
            return False
        else:
            # check that the specialization matches
            if base_case is not cls.base_case:
                return False
            # except EG:
            # issubclass(EG[???], EG)
            # the base class is the superclass of all its specializations
            if cls.specializations is None:
                return True
            # except EG[XXX]:
            # issubclass(EG[???], EG[XXX])
            # the superclass specialization must be at least
            # as general as the subclass specialization
            else:
                return cls._subclasscheck_specialization(subclass)

    def _subclasscheck_specialization(cls, subclass: "ExceptionGroupMeta"):
        """``issubclass(:Type[subclass.specialization], Type[:cls.specialization])``"""
        # specializations are covariant - if A <: B, then Class[A] <: Class[B]
        #
        # This means that we must handle cases where specializations
        # match multiple times - for example, when matching
        # Class[B] against Class[A, B], then B matches both A and B,
        #
        # Make sure that every specialization of ``cls`` matches something
        matched_specializations = all(
            any(
                issubclass(child, specialization)
                for child in subclass.specializations
            )
            for specialization in cls.specializations
        )
        # issubclass(EG[A, B], EG[A, C])
        if not matched_specializations:
            return False
        # issubclass(EG[A, B], EG[A, ...])
        elif cls.inclusive:
            # We do not care if ``subclass`` has unmatched specializations
            return True
        # issubclass(EG[A, B], EG[A, B]) vs issubclass(EG[A, B, C], EG[A, B])
        else:
            # Make sure that ``subclass`` has no unmatched specializations
            #
            # We need to check every child of subclass instead of comparing counts.
            # This is needed in case that we have duplicate matches. Consider:
            # EG[KeyError, LookupError], EG[KeyError, RuntimeError]
            return not any(
                not issubclass(child, cls.specializations)
                for child in subclass.specializations
            )

    # specialization Interface
    # Allows to do ``Cls[A, B, C]`` to specialize ``Cls`` with ``A, B, C``.
    # This part is the only one that actually understands ``...``.
    #
    # Expect this to be called by user-facing code, either directly or as a result
    # of ``Cls(A(), B(), C())``. Errors should be reported appropriately.
    def __getitem__(
        cls,
        item: Union[  # [Exception] or [...] or [Exception, ...]
            Type[BaseException],
            "ellipsis",
            Tuple[Union[Type[BaseException], "ellipsis"], ...],
        ],
    ):
        """``cls[item]`` - specialize ``cls`` with ``item``"""
        # validate/normalize parameters
        #
        # Cls[A, B][C]
        if cls.specializations is not None:
            raise TypeError(
                f"Cannot specialize already specialized {cls.__name__!r}"
            )
        # Cls[...]
        if item is ...:
            return cls
        # Cls[item]
        elif type(item) is not tuple:
            if not issubclass(item, BaseException):
                raise TypeError(
                    f"expected a BaseException subclass, not {item!r}"
                )
            item = (item,)
        # Cls[item1, item2]
        else:
            if not all(
                (child is ...) or issubclass(child, BaseException)
                for child in item
            ):
                raise TypeError(
                    f"expected a tuple of BaseException subclasses, not {item!r}"
                )
        return cls._get_specialization(item)

    def _get_specialization(cls, item):
        # provide specialized class
        #
        # If a type already exists for the given specialization, we return that
        # same type. This avoids class creation and allows fast `A is B` checks.
        # TODO: can this be moved before the expensive validation?
        unique_spec = frozenset(item)
        try:
            specialized_cls = cls._specs_cache[unique_spec]
        except KeyError:
            inclusive = ... in unique_spec
            specializations = tuple(
                child for child in unique_spec if child is not ...
            )
            # the specialization string "KeyError, IndexError, ..."
            spec = ", ".join(child.__name__ for child in specializations) + (
                ", ..." if inclusive else ""
            )
            # Note: type(name, bases, namespace) parameters cannot be passed by keyword
            specialized_cls = ExceptionGroupMeta(
                f"{cls.__name__}[{spec}]",
                (cls,),
                {},
                specializations=specializations,
                inclusive=inclusive,
            )
            cls._specs_cache[unique_spec] = specialized_cls
        return specialized_cls

    def __repr__(cls):
        return f"<class '{cls.__name__}'>"


class ExceptionGroup(BaseException, metaclass=ExceptionGroupMeta):
    """An exception that contains other exceptions.

    Its main use is to represent the situation when multiple child tasks all
    raise errors "in parallel".

    Args:
      message: A description of the overall exception.
      exceptions: The exceptions.
      sources: For each exception, a string describing where it came
        from.

    Raises:
      TypeError: if any of the passed in objects are not instances of
          :exc:`BaseException`.
      ValueError: if the exceptions and sources lists don't have the same
          length.

    """

    # metaclass instance fields - keep in sync with ExceptionGroupMeta
    #: the base case, i.e. this class
    base_case: ClassVar[ExceptionGroupMeta]
    #: whether additional child exceptions are allowed in issubclass checking
    inclusive: ClassVar[bool]
    #: the specialization of some class - e.g. (TypeError,) for Class[TypeError]
    #: or None for the base case
    specializations: ClassVar[Optional[Tuple[Type[BaseException], ...]]]
    #: internal cache for currently used specializations, i.e. mapping spec: Class[spec]
    _specs_cache = WeakValueDictionary()
    # instance fields
    message: str
    exceptions: Tuple[BaseException]
    sources: Tuple

    # __new__ automatically specialises ExceptionGroup to match its children.
    # ExceptionGroup(A(), B()) => ExceptionGroup[A, B](A(), B())
    def __new__(
        cls: "Type[ExceptionGroup]",
        message: str,
        exceptions: Sequence[BaseException],
        sources,
    ):
        if cls.specializations is not None:
            # forbid EG[A, B, C]()
            if not exceptions:
                raise TypeError(
                    f"specialisation of {cls.specializations} does not match"
                    f" empty exceptions; Note: Do not 'raise {cls.__name__}'"
                )
            # TODO: forbid EG[A, B, C](d, e, f, g)
            return super().__new__(cls)
        special_cls = cls[tuple(type(child) for child in exceptions)]
        return super().__new__(special_cls)

    def __init__(self, message: str, exceptions, sources):
        super().__init__(message, exceptions, sources)
        self.exceptions = tuple(exceptions)
        for exc in self.exceptions:
            if not isinstance(exc, Exception):
                raise TypeError(
                    "Expected an exception object, not {!r}".format(exc)
                )
        self.message = message
        self.sources = tuple(sources)
        if len(self.sources) != len(self.exceptions):
            raise ValueError(
                "different number of sources ({}) and exceptions ({})".format(
                    len(self.sources), len(self.exceptions)
                )
            )

    # copy.copy doesn't work for ExceptionGroup, because BaseException have
    # rewrite __reduce_ex__ method.  We need to add __copy__ method to
    # make it can be copied.
    def __copy__(self):
        new_group = self.__class__(self.message, self.exceptions, self.sources)
        new_group.__traceback__ = self.__traceback__
        new_group.__context__ = self.__context__
        new_group.__cause__ = self.__cause__
        # Setting __cause__ also implicitly sets the __suppress_context__
        # attribute to True.  So we should copy __suppress_context__ attribute
        # last, after copying __cause__.
        new_group.__suppress_context__ = self.__suppress_context__
        return new_group

    def __str__(self):
        return ", ".join(repr(exc) for exc in self.exceptions)

    def __repr__(self):
        return "<ExceptionGroup: {}>".format(self)
