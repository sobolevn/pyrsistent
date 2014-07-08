from collections import Sequence, Mapping, Set, Hashable
from itertools import chain
from functools import wraps
from numbers import Integral

def _bitcount(val):
    return bin(val).count("1")

BRANCH_FACTOR = 32
BIT_MASK = BRANCH_FACTOR - 1
SHIFT = _bitcount(BIT_MASK)


def _comparator(f):
    @wraps(f)
    def wrapper(*args, **kwds):
        if isinstance(args[0], PVector) and isinstance(args[1], PVector): 
            return f(*args, **kwds)
        return NotImplemented
    return wrapper


class PVector(object):
    """
    Do not instantiate directly, instead use the factory functions :py:func:`v` and :func:`pvector` to
    create an instance.

    Heavily influenced by the persistent vector available in Clojure. Initially this was more or
    less just a port of the Java code for the Clojure data structures. It has since been modified and to
    some extent optimized for usage in Python.

    The vector is organized as a trie, any mutating method will return a new vector that contains the changes. No
    updates are done to the original vector. Structural sharing between vectors are applied where possible to save
    space and to avoid making complete copies.

    This structure corresponds most closely to the built in list type and is intended as a replacement. Where the
    semantics are the same (more or less) the same function names have been used but for some cases it is not possible,
    for example assignments.

    The PVector implements the Sequence protocol and is Hashable.

    The following are examples of some common operations on persistent vectors:

    >>> p = v(1, 2, 3)
    >>> p2 = p.append(4)
    >>> p3 = p2.extend([5, 6, 7])
    >>> p
    (1, 2, 3)
    >>> p2
    (1, 2, 3, 4)
    >>> p3
    (1, 2, 3, 4, 5, 6, 7)
    >>> p3[5]
    6
    >>> p.assoc(1, 99)
    (1, 99, 3)
    >>>
    """
    __slots__ = ('_count', '_shift', '_root', '_tail', '_tail_offset')

    def __new__(cls, c, s, r, t):
        self = super(PVector, cls).__new__(cls)
        self._count = c
        self._shift = s
        self._root = r
        self._tail = t

        # Derived attribute stored for performance
        self._tail_offset = self._count - len(self._tail)
        return self

    def __len__(self):
        """
        >>> len(v(1, 2, 3))
        3
        """
        return self._count

    def __getitem__(self, index):
        """
        Get value at index. Full slicing support.

        >>> v1 = v(5, 6, 7, 8)
        >>> v1[2]
        7
        >>> v1[1:3]
        (6, 7)
        """
        if isinstance(index, slice):
            # There are more conditions than the below where it would be OK to
            # return ourselves, implement those...
            if index.start is None and index.stop is None and index.step is None:
                return self

            # This is a bit nasty realizing the whole structure as a list before
            # slicing it but it is the fastest way I've found to date, and it's easy :-)
            return _pvector(self._tolist()[index])

        if index < 0:
            index += self._count

        return self._node_for(index)[index & BIT_MASK]

    def __add__(self, other):
        """
        >>> v1 = v(1, 2)
        >>> v2 = v(3, 4)
        >>> v1 + v2
        (1, 2, 3, 4)
        """
        return self.extend(other)

    def __repr__(self):
        return str(self._totuple())

    __str__ = __repr__

    def __iter__(self):
        # This is kind of lazy and will produce some memory overhead but it is the fasted method
        # by far of those tried since it uses the speed of the built in python list directly.
        return iter(self._tolist())

    @_comparator
    def __ne__(self, other):
        return self._tolist() != other._tolist()

    @_comparator
    def __eq__(self, other):
        return self is other or self._tolist() == other._tolist()

    @_comparator
    def __gt__(self, other):
        return self._tolist() > other._tolist()

    @_comparator
    def __lt__(self, other):
        return self._tolist() < other._tolist()

    @_comparator
    def __ge__(self, other):
        return self._tolist() >= other._tolist()

    @_comparator
    def __le__(self, other):
        return self._tolist() <= other._tolist()

    def __mul__(self, times):
        """
        >>> v1 = v(1, 2)
        >>> 3 * v1
        (1, 2, 1, 2, 1, 2)
        """
        if times <= 0 or self is _EMPTY_VECTOR:
            return _EMPTY_VECTOR
        elif times == 1:
            return self
        else:
            return _pvector(times * self._tolist())

    __rmul__ = __mul__

    def _fill_list(self, node, shift, the_list):
        if shift:
            shift -= SHIFT
            for n in node:
                self._fill_list(n, shift, the_list)
        else:
            the_list.extend(node)

    def _tolist(self):
        """
        The fastest way to convert the vector into a python list.
        """
        the_list = []
        self._fill_list(self._root, self._shift, the_list)
        the_list.extend(self._tail)
        return the_list

    def _totuple(self):
        """
        Returns the content as a python tuple.
        """
        return tuple(self._tolist())

    def __hash__(self):
        """
        >>> v1 = v(1, 2, 3)
        >>> v2 = v(1, 2, 3)
        >>> hash(v1) == hash(v2)
        True
        """
        # Taking the easy way out again...
        return hash(self._totuple())

    @staticmethod
    def _make_persistent(val):
        if isinstance(val, list):
            return _pvector(val, recur=True)
        elif isinstance(val, dict):
            return pmap(val, recur=True)
        else:
            return val

    def assoc(self, i, val, recur=False):
        """
        Return a new vector with element at position i replaced with val. The first vector remains unchanged.

        Associng a value one step beyond the end of the vector is equal to appending. Associng beyond that will
        result in an IndexError.

        >>> v1 = v(1, 2, 3)
        >>> v1.assoc(1, 4)
        (1, 4, 3)
        >>> v1.assoc(3, 4)
        (1, 2, 3, 4)
        >>> v1.assoc(-1, 4)
        (1, 2, 4)
        """
        if not isinstance(i, Integral):
            raise TypeError("'%s' object cannot be interpreted as an index" % type(i).__name__)

        if i < 0:
            i += self._count

        if 0 <= i < self._count:
            if recur:
                val = self._make_persistent(val)

            if i >= self._tail_offset:
                new_tail = list(self._tail)
                new_tail[i & BIT_MASK] = val
                return PVector(self._count, self._shift, self._root, new_tail)

            return PVector(self._count, self._shift, self._do_assoc(self._shift, self._root, i, val), self._tail)

        if i == self._count:
            return self.append(val, recur)

        raise IndexError()

    def _do_assoc(self, level, node, i, val):
        ret = list(node)
        if level == 0:
            ret[i & BIT_MASK] = val
        else:
            sub_index = (i >> level) & BIT_MASK  # >>>
            ret[sub_index] = self._do_assoc(level - SHIFT, node[sub_index], i, val)

        return ret

    def _node_for(self, i):
        if 0 <= i < self._count:
            if i >= self._tail_offset:
                return self._tail

            node = self._root
            for level in range(self._shift, 0, -SHIFT):
                node = node[(i >> level) & BIT_MASK]  # >>>

            return node

        raise IndexError()

    def _create_new_root(self):
        new_shift = self._shift

        # Overflow root?
        if (self._count >> SHIFT) > (1 << self._shift): # >>>
            new_root = [self._root, self._new_path(self._shift, self._tail)]
            new_shift += SHIFT
        else:
            new_root = self._push_tail(self._shift, self._root, self._tail)

        return new_root, new_shift

    def append(self, val, recur=False):
        """
        Return a new vector with val appended.

        >>> v1 = v(1, 2)
        >>> v1.append(3)
        (1, 2, 3)
        """
        if recur:
            val = self._make_persistent(val)

        if len(self._tail) < BRANCH_FACTOR:
            new_tail = list(self._tail)
            new_tail.append(val)
            return PVector(self._count + 1, self._shift, self._root, new_tail)

        # Full tail, push into tree
        new_root, new_shift = self._create_new_root()
        return PVector(self._count + 1, new_shift, new_root, [val])

    def _new_path(self, level, node):
        if level == 0:
            return node

        return [self._new_path(level - SHIFT, node)]

    def _mutating_insert_tail(self):
        self._root, self._shift = self._create_new_root()
        self._tail = []

    def _mutating_fill_tail(self, offset, sequence, recur):
        max_delta_len = BRANCH_FACTOR - len(self._tail)
        if recur:
            delta = [self._make_persistent(val) for val in sequence[offset:offset + max_delta_len]]
        else:
            delta = sequence[offset:offset + max_delta_len]
        self._tail.extend(delta)
        delta_len = len(delta)
        self._count += delta_len
        return offset + delta_len

    def _mutating_extend(self, sequence, recur):
        offset = 0
        sequence_len = len(sequence)
        while offset < sequence_len:
            offset = self._mutating_fill_tail(offset, sequence, recur)
            if len(self._tail) == BRANCH_FACTOR:
                self._mutating_insert_tail()

        self._tail_offset = self._count - len(self._tail)

    def extend(self, obj, recur=False):
        """
        Return a new vector with all values in obj appended to it. Obj may be another
        PVector or any other Iterable.

        >>> v1 = v(1, 2, 3)
        >>> v1.extend([4, 5])
        (1, 2, 3, 4, 5)
        """
        # Mutates the new vector directly for efficiency but that's only an
        # implementation detail, once it is returned it should be considered immutable
        l = obj._tolist() if isinstance(obj, PVector) else list(obj)
        if l:
            new_vector = self.append(l[0], recur=recur)
            new_vector._mutating_extend(l[1:], recur=recur)
            return new_vector

        return self

    def _push_tail(self, level, parent, tail_node):
        """
        if parent is leaf, insert node,
        else does it map to an existing child? ->
             node_to_insert = push node one more level
        else alloc new path

        return  node_to_insert placed in copy of parent
        """
        ret = list(parent)

        if level == SHIFT:
            ret.append(tail_node)
            return ret

        sub_index = ((self._count - 1) >> level) & BIT_MASK  # >>>
        if len(parent) > sub_index:
            ret[sub_index] = self._push_tail(level - SHIFT, parent[sub_index], tail_node)
            return ret

        ret.append(self._new_path(level - SHIFT, tail_node))
        return ret

    def assoc_in(self, keys, val):
        """
        Insert val into nested persistent structure at position specified by Iterable keys. Any levels that
        do not exist will be inserted as new PMaps.

        >>> v1 = v(1, 2, m(a=5, b=6))
        >>> v1.assoc_in((2, 'b'), 17)
        (1, 2, {'a': 5, 'b': 17})
        >>> v1.assoc_in((2, 'c', 'd'), 17)
        (1, 2, {'a': 5, 'c': {'d': 17}, 'b': 6})
        """
        if not keys:
            return self
        elif len(keys) == 1:
            return self.assoc(keys[0], val)
        elif keys[0] == self._count:
            return self.append(pmap().assoc_in(keys[1:], val))
        else:
            return self.assoc(keys[0], self[keys[0]].assoc_in(keys[1:], val))

    def index(self, value, *args, **kwargs):
        """
        Return first index of value. Additional indexes may be supplied to limit the search to a
        sub range of the vector.
        
        >>> v1 = v(1, 2, 3, 4, 3)
        >>> v1.index(3)
        2
        >>> v1.index(3, 3, 5)
        4
        """
        return self._tolist().index(value, *args, **kwargs)

    def count(self, value):
        """
        Return the number of times that value appears in the vector.

        >>> v1 = v(1, 4, 3, 4)
        >>> v1.count(4)
        2
        """
        return self._tolist().count(value)

Sequence.register(PVector)
Hashable.register(PVector)

_EMPTY_VECTOR = PVector(0, SHIFT, [], [])


def _pvector(sequence=(), recur=False):
    """
    Factory function, returns a new PVector object containing the elements in sequence.

    >>> v1 = pvector([1, 2, 3])
    >>> v1
    (1, 2, 3)
    """
    return _EMPTY_VECTOR.extend(sequence, recur)


pvector = _pvector
try:
    # Use the C extension as underlying implementation if it is available
    from pvectorc import pvector as pvector_c
    pvector = pvector
except ImportError:
    pass


def v(*elements):
    """
    Factory function, returns a new PVector object containing all parameters.

    >>> v1 = v(1, 2, 3)
    >>> v1
    (1, 2, 3)
    """
    return pvector(elements)


####################### PMap #####################################
class PMap(object):
    """
    Do not instantiate directly, instead use the factory functions :py:func:`m` or :py:func:`pmap` to
    create an instance.

    Persistent map/dict. Tries to follow the same naming conventions as the built in dict where feasible.

    Was originally written as a very close copy of the Clojure equivalent but was later rewritten to closer
    re-assemble the python dict. This means that a sparse vector (a PVector) of buckets is used. The keys are
    hashed and the elements inserted at position hash % len(bucket_vector). Whenever the map size exceeds 2/3 of
    the containing vectors size the map is reallocated to a vector of double the size. This is done to avoid
    excessive hash collisions.

    This structure corresponds most closely to the built in dict type and is intended as a replacement. Where the
    semantics are the same (more or less) the same function names have been used but for some cases it is not possible,
    for example assignments and deletion of values.

    PMap implements the Mapping protocol and is Hashable.

    The following are examples of some common operations on persistent maps

    >>> m1 = m(a=1, b=3)
    >>> m2 = m1.assoc('c', 3)
    >>> m3 = m2.dissoc('a')
    >>> m1
    {'a': 1, 'b': 3}
    >>> m2
    {'a': 1, 'c': 3, 'b': 3}
    >>> m3
    {'c': 3, 'b': 3}
    >>> m3['c']
    3
    """
    __slots__ = ('_size', '_buckets')

    def __init__(self, size, buckets):
        self._size = size
        self._buckets = buckets

    def _get_bucket(self, key):
        index = hash(key) % len(self._buckets)
        bucket = self._buckets[index]
        return index, bucket

    def __getitem__(self, key):
        _, bucket = self._get_bucket(key)
        if bucket:
            for k, v in bucket:
                if k == key:
                    return v

        raise KeyError

    def __contains__(self, key):
        _, bucket = self._get_bucket(key)
        if bucket:
            for k, _ in bucket:
                if k == key:
                    return True

            return False

        return False

    get = Mapping.get

    def __iter__(self):
        return self.iterkeys()

    def __getattr__(self, key):
        return self[key]

    def iterkeys(self):
        for k, _ in self.iteritems():
            yield k

    # These are more efficient implementations compared to the original
    # methods that are based on the keys iterator and then calls the
    # accessor functions to access the value for the corresponding key
    def itervalues(self):
        for _, v in self.iteritems():
            yield v

    def iteritems(self):
        for bucket in self._buckets:
            if bucket:
                for k, v in bucket:
                    yield k, v

    def values(self):
        return list(self.itervalues())

    def keys(self):
        return list(self.iterkeys())

    def items(self):
        return list(self.iteritems())

    def __len__(self):
        return self._size

    def __repr__(self):
        return str(dict(self))

    __eq__ = Mapping.__eq__
    __ne__ = Mapping.__ne__
    __str__ = __repr__

    def __hash__(self):
        # This hashing algorithm is probably not the speediest
        return hash(frozenset(self.iteritems()))

    def assoc(self, key, val):
        """
        Return a new PMap with key and val inserted.

        >>> m1 = m(a=1, b=2)
        >>> m2 = m1.assoc('a', 3)
        >>> m3 = m1.assoc('c' ,4)
        >>> m1
        {'a': 1, 'b': 2}
        >>> m2
        {'a': 3, 'b': 2}
        >>> m3
        {'a': 1, 'c': 4, 'b': 2}
        """
        kv = (key, val)
        index, bucket = self._get_bucket(key)
        if bucket:
            for k, v in bucket:
                if k == key:
                    if v is val:
                        return self
                    else:
                        new_bucket = [(k2, v2) if k2 != k else (k2, val) for k2, v2 in bucket]
                        return PMap(self._size, self._buckets.assoc(index, new_bucket))

            if len(self._buckets) < 0.67 * self._size:
                return PMap(self._size, self._reallocate(2 * len(self._buckets))).assoc(key, val)
            else:
                new_bucket = [kv]
                new_bucket.extend(bucket)
                new_buckets = self._buckets.assoc(index, new_bucket)

            return PMap(self._size + 1, new_buckets)

        # Skip reallocation check if there was no conflict
        return PMap(self._size + 1, self._buckets.assoc(index, [kv]))

    def dissoc(self, key):
        """
        Return a new PMap without the element specified by key. Returns reference to itself
        if element is not present.

        >>> m1 = m(a=1, b=2)
        >>> m1.dissoc('a')
        {'b': 2}
        >>> m1 is m1.dissoc('c')
        True
        """

        # Should shrinking of the map ever be done if it becomes very small?
        index, bucket = self._get_bucket(key)

        if bucket:
            new_bucket = [(k, v) for (k, v) in bucket if k != key]
            if len(bucket) > len(new_bucket):
                return PMap(self._size - 1, self._buckets.assoc(index, new_bucket if new_bucket else None))

        return self

    def merge(self, *maps):
        """
        Return a new PMap with the items in Mappings inserted. If the same key is present in multiple
        maps the rightmost (last) value is inserted.

        >>> m1 = m(a=1, b=2)
        >>> m1.merge(m(a=2, c=3), {'a': 17, 'd': 35})
        {'a': 17, 'c': 3, 'b': 2, 'd': 35}
        """
        # Optimization opportunities here
        if not maps:
            return self
        elif len(maps) > 1:
            merge_map = dict(maps[0])
            for m in maps[1:]:
                merge_map.update(m)
        else:
            merge_map = maps[0]

        result = self
        for k, v in merge_map.items():
            result = result.assoc(k, v)
        
        return result

    def assoc_in(self, keys, val):
        """
        Insert val into nested persistent structure at position specified by Iterable keys. Any levels that
        do not exist will be inserted as new PMaps.

        >>> m1 = m(a=5, b=6, c=v(1, 2))
        >>> m1.assoc_in(('c', 1), 17)
        {'a': 5, 'c': (1, 17), 'b': 6}
        """
        if not keys:
            return self
        elif len(keys) == 1:
            return self.assoc(keys[0], val)
        else:
            return self.assoc(keys[0], self.get(keys[0], _EMPTY_PMAP).assoc_in(keys[1:], val))

    def _reallocate_to_list(self, new_size):
        new_list = new_size * [None]
        for k, v in chain.from_iterable(x for x in self._buckets if x):
            index = hash(k) % new_size
            if new_list[index]:
                new_list[index].append((k, v))
            else:
                new_list[index] = [(k, v)]

        return new_list

    def _reallocate(self, new_size):
        return pvector(self._reallocate_to_list(new_size))

Mapping.register(PMap)
Hashable.register(PMap)


def _turbo_mapping(initial, pre_size):
    size = pre_size or (2 * len(initial)) or 8
    buckets = size * [None]

    if not isinstance(initial, Mapping):
        # Make a dictionary of the initial data if it isn't already,
        # that will save us some job further down since we can assume no
        # key collisions
        initial = dict(initial)

    for k, v in initial.iteritems():
        h = hash(k)
        index = h % size
        bucket = buckets[index]

        if bucket:
            bucket.append((k, v))
        else:
            buckets[index] = [(k, v)]

    return PMap(len(initial), pvector(buckets))


_EMPTY_PMAP = _turbo_mapping({}, 0)


def pmap(initial={}, pre_size=0, recur=False):
    """
    Factory function, inserts all elements in initial into the newly created map.
    The optional argument pre_size may be used to specify an initial size of the underlying bucket vector. This
    may have a positive performance impact in the cases where you know beforehand that a large number of elements
    will be inserted into the map eventually since it will reduce the number of reallocations required.

    >>> pmap({'a': 13, 'b': '14'})
    {'a': 13, 'b': '14'}
    """
    if not initial:
        return _EMPTY_PMAP

    return _turbo_mapping(initial, pre_size)


def m(**kwargs):
    """
    Factory function, inserts all key value arguments into the newly created map.

    >>> m(a=13, b=14)
    {'a': 13, 'b': 14}
    """
    return pmap(kwargs)

##################### PSet ########################

class PSet(object):
    """
    Do not instantiate directly, instead use the factory functions :py:func:`s` or :py:func:`pset`
    to create an instance.

    Persistent set implementation. Built on top of the persistent map. The set supports all operations
    in the Set protocol and is Hashable.

    Some examples:

    >>> s = pset([1, 2, 3, 1])
    >>> s2 = s.add(4)
    >>> s3 = s2.remove(2)
    >>> s
    pset([1, 2, 3])
    >>> s2
    pset([1, 2, 3, 4])
    >>> s3
    pset([1, 3, 4])
    """
    __slots__ = ('_map',)

    def __init__(self, m):
        self._map = m

    def __contains__(self, element):
        return element in self._map

    def __iter__(self):
        return iter(self._map)

    def __len__(self):
        return len(self._map)

    def __repr__(self):
        return 'p' + str(set(self))

    __str__ = __repr__

    def __hash__(self):
        return hash(self._map)

    @classmethod
    def _from_iterable(cls, it, pre_size=8):
        return PSet(pmap({k: True for k in it}, pre_size=pre_size))

    def add(self, element):
        """
        Return a new PSet with element added

        >>> s1 = s(1, 2)
        >>> s1.add(3)
        pset([1, 2, 3])
        """
        return PSet(self._map.assoc(element, True))

    def remove(self, element):
        """
        Return a new PSet with element removed. Raises KeyError if element is not present.

        >>> s1 = s(1, 2)
        >>> s1.remove(2)
        pset([1])
        """
        if element in self._map:
            return PSet(self._map.dissoc(element))

        raise KeyError("Element '%s' not present in PSet" % element)

    def discard(self, element):
        """
        Return a new PSet with element removed. Returns itself if element is not present.
        """
        if element in self._map:
            return PSet(self._map.dissoc(element))

        return self

    # All the operations and comparisons you would expect on a set.
    #
    # This is not very beautiful. If we avoid inheriting from PSet we can use the
    # __slots__ concepts (which requires a new style class) and hopefully save some memory.
    __le__ = Set.__le__
    __lt__ = Set.__lt__
    __gt__ = Set.__gt__
    __ge__ = Set.__ge__
    __eq__ = Set.__eq__
    __ne__ = Set.__ne__

    __and__ = Set.__and__
    __or__ = Set.__or__
    __sub__ = Set.__sub__
    __xor__ = Set.__xor__

    issubset = __le__
    issuperset = __ge__
    union = __or__
    intersection = __and__
    difference = __sub__
    symmetric_difference = __xor__

    isdisjoint = Set.isdisjoint

Set.register(PSet)
Hashable.register(PSet)

_EMPTY_PSET = PSet(_EMPTY_PMAP)


def pset(sequence=(), pre_size=8):
    """
    Factory function, takes an iterable with elements to insert and optionally a sizing parameter equivalent to that
    used for :py:func:`pmap`.

    >>> s1 = pset([1, 2, 3, 2])
    >>> s1
    pset([1, 2, 3])
    """
    if not sequence:
        return _EMPTY_PSET

    return PSet._from_iterable(sequence, pre_size=pre_size)


def s(*args):
    """
    Factory function for persistent sets.

    >>> s1 = s(1, 2, 3, 2)
    >>> s1
    pset([1, 2, 3])
    """
    return pset(args)


######################################## Immutable object ##############################################


def immutable(members='', name='Immutable', verbose=False):
    """
    Produces a class that either can be used standalone or as a base class for immutable classes.

    This is a thin wrapper around a named tuple.

    Constructing a type and using it to instantiate objects:

    >>> Point = immutable('x, y', name='Point')
    >>> p = Point(1, 2)
    >>> p2 = p.set(x=3)
    >>> p
    Point(x=1, y=2)
    >>> p2
    Point(x=3, y=2)

    Inheriting from a constructed type. In this case no type name needs to be supplied:

    >>> class PositivePoint(immutable('x, y')):
    ...     __slots__ = tuple()
    ...     def __new__(cls, x, y):
    ...         if x > 0 and y > 0:
    ...             return super(PositivePoint, cls).__new__(cls, x, y)
    ...         raise Exception('Coordinates must be positive!')
    ...
    >>> p = PositivePoint(1, 2)
    >>> p.set(x=3)
    PositivePoint(x=3, y=2)
    >>> p.set(y=-3)
    Traceback (most recent call last):
    Exception: Coordinates must be positive!

    The immutable class also supports the notion of frozen members. The value of a frozen members
    cannot be updated. For example it could be used to implement an ID that should remain the same
    over time. A frozen member is denoted by a trailing underscore.

    >>> Point = immutable('x, y, id_', name='Point')
    >>> p = Point(1, 2, id_=17)
    >>> p.set(x=3)
    Point(x=3, y=2, id_=17)
    >>> p.set(id_=18)
    Traceback (most recent call last):
    AttributeError: Cannot set frozen members id_
    """

    if isinstance(members, basestring):
        members = members.replace(',', ' ').split()

    def frozen_member_test():
        frozen_members = ["'%s'" % f for f in members if f.endswith('_')]
        if frozen_members:
            return """
        frozen_fields = fields_to_modify & {{{frozen_members}}}
        if frozen_fields:
            raise AttributeError('Cannot set frozen members %s' % ', '.join(frozen_fields))
            """.format(frozen_members=', '.join(frozen_members))

        return ''

    quoted_members = ', '.join("'%s'" % m for m in members)
    template = """
class {class_name}(namedtuple('ImmutableBase', [{quoted_members}], verbose={verbose})):
    __slots__ = tuple()

    def __repr__(self):
        return super({class_name}, self).__repr__().replace('ImmutableBase', self.__class__.__name__)

    def set(self, **kwargs):
        if not kwargs:
            return self

        fields_to_modify = set(kwargs.keys())
        if not fields_to_modify <= {member_set}:
            raise AttributeError("'%s' is not a member" % ', '.join(fields_to_modify - {member_set}))

        {frozen_member_test}

        return self.__class__.__new__(self.__class__, *map(kwargs.pop, [{quoted_members}], self))
    """.format(quoted_members=quoted_members,
               member_set="{%s}" % quoted_members if quoted_members else 'set()',
               frozen_member_test=frozen_member_test(),
               verbose=verbose,
               class_name=name)

    if verbose:
        print template

    from collections import namedtuple
    namespace = dict(namedtuple=namedtuple, __name__='pyrsistent_immutable')
    try:
        exec template in namespace
    except SyntaxError, e:
        raise SyntaxError(e.message + ':\n' + template)

    return namespace[name]
