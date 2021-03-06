"""Various helpful bits and pieces without much of a common theme."""

from __future__ import division

__copyright__ = "Copyright (C) 2010 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
"""



import numpy as np
from decorator import decorator
import pyopencl as cl
from pytools import memoize, memoize_method

import re

from pyopencl.compyte.dtypes import (
        get_or_register_dtype, TypeNameNotKnown,
        register_dtype, _fill_dtype_registry,
        dtype_to_ctype)

_fill_dtype_registry(respect_windows=False)
get_or_register_dtype("cfloat_t", np.complex64)
get_or_register_dtype("cdouble_t", np.complex128)




bitlog2 = cl.bitlog2

PooledBuffer = cl.PooledBuffer

from pyopencl._cl import _tools_DeferredAllocator as DeferredAllocator
from pyopencl._cl import _tools_ImmediateAllocator as ImmediateAllocator

class CLAllocator(DeferredAllocator):
    def __init__(self, *args, **kwargs):
        from warnings import warn
        warn("pyopencl.tools.CLAllocator is deprecated. "
                "It will be continue to exist throughout the 2013.x "
                "versions of PyOpenCL. Use {Deferred,Immediate}Allocator.",
                DeprecationWarning, 2)
        DeferredAllocator.__init__(self, *args, **kwargs)

MemoryPool = cl.MemoryPool




_first_arg_dependent_caches = []




@decorator
def first_arg_dependent_memoize(func, cl_object, *args):
    """Provides memoization for things that get created inside
    a context, i.e. mainly programs and kernels. Assumes that
    the first argument of the decorated function is an OpenCL
    object that might go away, such as a context or a queue,
    and based on which we might want to clear the cache.

    .. versionadded:: 2011.2
    """
    try:
        ctx_dict = func._pyopencl_first_arg_dep_memoize_dic
    except AttributeError:
        # FIXME: This may keep contexts alive longer than desired.
        # But I guess since the memory in them is freed, who cares.
        ctx_dict = func._pyopencl_first_arg_dep_memoize_dic = {}

    try:
        return ctx_dict[cl_object][args]
    except KeyError:
        _first_arg_dependent_caches.append(ctx_dict)
        arg_dict = ctx_dict.setdefault(cl_object, {})
        result = func(cl_object, *args)
        arg_dict[args] = result
        return result

context_dependent_memoize = first_arg_dependent_memoize




def clear_first_arg_caches():
    """Empties all first-argument-dependent memoization caches. Also releases
    all held reference contexts. If it is important to you that the
    program detaches from its context, you might need to call this
    function to free all remaining references to your context.

    .. versionadded:: 2011.2
    """
    for cache in _first_arg_dependent_caches:
        cache.clear()

import atexit
atexit.register(clear_first_arg_caches)




def get_test_platforms_and_devices(plat_dev_string=None):
    """Parse a string of the form 'PYOPENCL_TEST=0:0,1;intel:i5'.

    :return: list of tuples (platform, [device, device, ...])
    """

    if plat_dev_string is None:
        import os
        plat_dev_string = os.environ.get("PYOPENCL_TEST", None)

    def find_cl_obj(objs, identifier):
        try:
            num = int(identifier)
        except Exception:
            pass
        else:
            return objs[num]

        found = False
        for obj in objs:
            if identifier.lower() in (obj.name + ' ' + obj.vendor).lower():
                return obj
        if not found:
            raise RuntimeError("object '%s' not found" % identifier)

    if plat_dev_string:
        result = []

        for entry in plat_dev_string.split(";"):
            lhsrhs = entry.split(":")

            if len(lhsrhs) == 1:
                platform = find_cl_obj(cl.get_platforms(), lhsrhs[0])
                result.append((platform, platform.get_devices()))

            elif len(lhsrhs) != 2:
                raise RuntimeError("invalid syntax of PYOPENCL_TEST")
            else:
                plat_str, dev_strs = lhsrhs

                platform = find_cl_obj(cl.get_platforms(), plat_str)
                devs = platform.get_devices()
                result.append(
                        (platform, [find_cl_obj(devs, dev_id) for dev_id in dev_strs.split(",")]))

        return result

    else:
        return [
                (platform, platform.get_devices())
                for platform in cl.get_platforms()]




def pytest_generate_tests_for_pyopencl(metafunc):
    class ContextFactory:
        def __init__(self, device):
            self.device = device

        def __call__(self):
            # Get rid of leftovers from past tests.
            # CL implementations are surprisingly limited in how many
            # simultaneous contexts they allow...

            clear_first_arg_caches()

            from gc import collect
            collect()

            return cl.Context([self.device])

        def __str__(self):
            return "<context factory for %s>" % self.device

    test_plat_and_dev = get_test_platforms_and_devices()

    if ("device" in metafunc.funcargnames
            or "ctx_factory" in metafunc.funcargnames
            or "ctx_getter" in metafunc.funcargnames):
        arg_dict = {}

        for platform, plat_devs in test_plat_and_dev:
            if "platform" in metafunc.funcargnames:
                arg_dict["platform"] = platform

            for device in plat_devs:
                if "device" in metafunc.funcargnames:
                    arg_dict["device"] = device

                if "ctx_factory" in metafunc.funcargnames:
                    arg_dict["ctx_factory"] = ContextFactory(device)

                if "ctx_getter" in metafunc.funcargnames:
                    from warnings import warn
                    warn("The 'ctx_getter' arg is deprecated in favor of 'ctx_factory'.",
                            DeprecationWarning)
                    arg_dict["ctx_getter"] = ContextFactory(device)

                metafunc.addcall(funcargs=arg_dict.copy(),
                        id=", ".join("%s=%s" % (arg, value)
                                for arg, value in arg_dict.iteritems()))

    elif "platform" in metafunc.funcargnames:
        for platform, plat_devs in test_plat_and_dev:
            metafunc.addcall(
                    funcargs=dict(platform=platform),
                    id=str(platform))




# {{{ C argument lists

class Argument(object):
    pass

class DtypedArgument(Argument):
    def __init__(self, dtype, name):
        self.dtype = np.dtype(dtype)
        self.name = name

    def __repr__(self):
        return "%s(%r, %s)" % (
                self.__class__.__name__,
                self.name,
                self.dtype)

class VectorArg(DtypedArgument):
    def declarator(self):
        return "__global %s *%s" % (dtype_to_ctype(self.dtype), self.name)

class ScalarArg(DtypedArgument):
    def declarator(self):
        return "%s %s" % (dtype_to_ctype(self.dtype), self.name)

class OtherArg(Argument):
    def __init__(self, declarator, name):
        self.decl = declarator
        self.name = name

    def declarator(self):
        return self.decl





def parse_c_arg(c_arg):
    for aspace in ["__local", "__constant"]:
        if aspace in c_arg:
            raise RuntimeError("cannot deal with local or constant "
                    "OpenCL address spaces in C argument lists ")

    c_arg = c_arg.replace("__global", "")

    from pyopencl.compyte.dtypes import parse_c_arg_backend
    return parse_c_arg_backend(c_arg, ScalarArg, VectorArg)

def parse_arg_list(arguments):
    """Parse a list of kernel arguments. *arguments* may be a comma-separate list
    of C declarators in a string, a list of strings representing C declarators,
    or :class:`Argument` objects.
    """

    if isinstance(arguments, str):
        arguments = arguments.split(",")

    def parse_single_arg(obj):
        if isinstance(obj, str):
            from pyopencl.tools import parse_c_arg
            return parse_c_arg(obj)
        else:
            return obj

    return [parse_single_arg(arg) for arg in arguments]

def get_arg_list_scalar_arg_dtypes(arg_types):
    result = []

    from pyopencl.tools import ScalarArg
    for arg_type in arg_types:
        if isinstance(arg_type, ScalarArg):
            result.append(arg_type.dtype)
        else:
            result.append(None)

    return result

# }}}




def get_gl_sharing_context_properties():
    ctx_props = cl.context_properties

    from OpenGL import platform as gl_platform, GLX, WGL

    props = []

    import sys
    if sys.platform == "linux2":
        props.append(
            (ctx_props.GL_CONTEXT_KHR, gl_platform.GetCurrentContext()))
        props.append(
                (ctx_props.GLX_DISPLAY_KHR, 
                    GLX.glXGetCurrentDisplay()))
    elif sys.platform == "win32":
        props.append(
            (ctx_props.GL_CONTEXT_KHR, gl_platform.GetCurrentContext()))
        props.append(
                (ctx_props.WGL_HDC_KHR, 
                    WGL.wglGetCurrentDC()))
    elif sys.platform == "darwin":
        props.append(
            (ctx_props.CONTEXT_PROPERTY_USE_CGL_SHAREGROUP_APPLE, cl.get_apple_cgl_share_group()))
    else:
        raise NotImplementedError("platform '%s' not yet supported" 
                % sys.platform)

    return props




class _CDeclList:
    def __init__(self, device):
        self.device = device
        self.declared_dtypes = set()
        self.declarations = []
        self.saw_double = False
        self.saw_complex = False

    def add_dtype(self, dtype):
        dtype = np.dtype(dtype)

        if dtype in [np.float64 or np.complex128]:
            self.saw_double = True

        if dtype.kind == "c":
            self.saw_complex = True

        if dtype.kind != "V":
            return

        if dtype in self.declared_dtypes:
            return

        for name, (field_dtype, offset) in dtype.fields.iteritems():
            self.add_dtype(field_dtype)

        _, cdecl = match_dtype_to_c_struct(self.device, dtype_to_ctype(dtype), dtype)

        self.declarations.append(cdecl)
        self.declared_dtypes.add(dtype)

    def visit_arguments(self, arguments):
        for arg in arguments:
            dtype = arg.dtype
            if dtype in [np.float64 or np.complex128]:
                self.saw_double = True

            if dtype.kind == "c":
                self.saw_complex = True

    def get_declarations(self):
        result = "\n\n".join(self.declarations)

        if self.saw_double:
            result = (
                    "#pragma OPENCL EXTENSION cl_khr_fp64: enable\n"
                    "#define PYOPENCL_DEFINE_CDOUBLE\n"
                    + result)

        if self.saw_complex:
            result = (
                    "#include <pyopencl-complex.h>\n\n"
                    + result)

        return result


@memoize
def match_dtype_to_c_struct(device, name, dtype, context=None):
    """Return a tuple `(dtype, c_decl)` such that the C struct declaration
    in `c_decl` and the structure :class:`numpy.dtype` instance `dtype`
    have the same memory layout.

    Note that *dtype* may be modified from the value that was passed in,
    for example to insert padding.

    (As a remark on implementation, this routine runs a small kernel on
    the given *device* to ensure that :mod:`numpy` and C offsets and
    sizes match.)

    .. versionadded: 2013.1

    This example explains the use of this function::

        >>> import numpy as np
        >>> import pyopencl as cl
        >>> import pyopencl.tools
        >>> dtype = np.dtype([("id", np.uint32), ("value", np.float32)])
        >>> dtype, c_decl = pyopencl.tools.match_dtype_to_c_struct(ctx.devices[0], 'id_val', dtype)
        >>> print c_decl
        typedef struct {
          unsigned id;
          float value;
        } id_val;
        >>> print dtype
        [('id', '<u4'), ('value', '<f4')]
        >>> dtype_cl.tools.get_or_register_dtype('id_val', dtype)

    As this example shows, it is important to call :func:`get_or_register_dtype` on
    the modified `dtype` returned by this function, not the original one.
    """

    fields = sorted(dtype.fields.iteritems(),
            key=lambda (name, (dtype, offset)): offset)

    c_fields = []
    for field_name, (field_dtype, offset) in fields:
        c_fields.append("  %s %s;" % (dtype_to_ctype(field_dtype), field_name))

    c_decl = "typedef struct {\n%s\n} %s;" % (
            "\n".join(c_fields),
            name)

    cdl = _CDeclList(device)
    for field_name, (field_dtype, offset) in fields:
        cdl.add_dtype(field_dtype)

    pre_decls = cdl.get_declarations()

    offset_code = "\n".join(
            "result[%d] = pycl_offsetof(%s, %s);" % (i+1, name, field_name)
            for i, (field_name, (field_dtype, offset)) in enumerate(fields))

    src = r"""
        #define pycl_offsetof(st, m) \
                 ((size_t) ((__local char *) &(dummy.m) - (__local char *)&dummy ))

        %(pre_decls)s

        %(my_decl)s

        __kernel void get_size_and_offsets(__global size_t *result)
        {
            result[0] = sizeof(%(my_type)s);
            __local %(my_type)s dummy;
            %(offset_code)s
        }
    """ % dict(
            pre_decls=pre_decls,
            my_decl=c_decl,
            my_type=name,
            offset_code=offset_code)

    if context is None:
        context = cl.Context([device])

    queue = cl.CommandQueue(context)

    prg = cl.Program(context, src)
    knl = prg.build(devices=[device]).get_size_and_offsets

    import pyopencl.array
    result_buf = cl.array.empty(queue, 1+len(fields), np.uintp)
    knl(queue, (1,), (1,), result_buf.data)
    queue.finish()
    size_and_offsets = result_buf.get()

    size = int(size_and_offsets[0])

    from pytools import any
    offsets = size_and_offsets[1:]
    if any(ofs >= size for ofs in offsets):
        # offsets not plausible

        if dtype.itemsize == size:
            # If sizes match, use numpy's idea of the offsets.
            offsets = [offset
                    for field_name, (field_dtype, offset) in fields]
        else:
            raise RuntimeError("cannot discover struct layout on '%s'" % device)

    result_buf.data.release()
    del knl
    del prg
    del queue
    del context

    dtype_arg_dict = dict(
            names=[field_name for field_name, (field_dtype, offset) in fields],
            formats=[field_dtype for field_name, (field_dtype, offset) in fields],
            offsets=[int(x) for x in offsets],
            itemsize=int(size_and_offsets[0]),
            )
    dtype = np.dtype(dtype_arg_dict)

    if dtype.itemsize != size_and_offsets[0]:
        # "Old" versions of numpy (1.6.x?) silently ignore "itemsize". Boo.
        dtype_arg_dict["names"].append("_pycl_size_fixer")
        dtype_arg_dict["formats"].append(np.uint8)
        dtype_arg_dict["offsets"].append(int(size_and_offsets[0])-1)
        dtype = np.dtype(dtype_arg_dict)

    assert dtype.itemsize == size_and_offsets[0]

    return dtype, c_decl




@memoize
def dtype_to_c_struct(device, dtype):
    matched_dtype, c_decl = match_dtype_to_c_struct(
            device, dtype_to_ctype(dtype), dtype)

    def dtypes_match():
        result = len(dtype.fields) == len(matched_dtype.fields)

        for name, val in dtype.fields.iteritems():
            result = result and matched_dtype.fields[name] == val

        return result

    assert dtypes_match()

    return c_decl



# {{{ code generation/templating helper

def _process_code_for_macro(code):
    code = code.replace("//CL//", "\n")

    if "//" in code:
        raise RuntimeError("end-of-line comments ('//') may not be used in "
                "code snippets")

    return code.replace("\n", " \\\n")

class _SimpleTextTemplate:
    def __init__(self, txt):
        self.txt = txt

    def render(self, context):
        return self.txt

class _PrintfTextTemplate:
    def __init__(self, txt):
        self.txt = txt

    def render(self, context):
        return self.txt % context

class _MakoTextTemplate:
    def __init__(self, txt):
        from mako.template import Template
        self.template = Template(txt, strict_undefined=True)

    def render(self, context):
        return self.template.render(**context)




class _ArgumentPlaceholder:
    def __init__(self, typename, name):
        self.typename = typename
        self.name = name

class _VectorArgPlaceholder(_ArgumentPlaceholder):
    target_class = VectorArg

class _ScalarArgPlaceholder(_ArgumentPlaceholder):
    target_class = ScalarArg




class _TemplateRenderer(object):
    def __init__(self, template, type_values, var_values, context=None, options=[]):
        self.template = template
        self.type_dict = dict(type_values)
        self.var_dict = dict(var_values)

        for name in self.var_dict:
            if name.startswith("macro_"):
                self.var_dict[name] = _process_code_for_macro(self.var_dict[name])

        self.context = context
        self.options = options

    def __call__(self, txt):
        if txt is None:
            return txt

        result = self.template.get_text_template(txt).render(self.var_dict)

        # substitute in types
        for name, dtype in self.type_dict.iteritems():
            result = re.sub(r"\b%s\b" % name, dtype_to_ctype(dtype), result)

        return str(result)

    def get_rendered_kernel(self, txt, kernel_name):
        prg = cl.Program(self.context, self(txt)).build(self.options)

        kernel_name_prefix = self.var_dict.get("kernel_name_prefix")
        if kernel_name_prefix is not None:
            kernel_name = kernel_name_prefix+kernel_name

        return getattr(prg, kernel_name)

    def parse_type(self, typename):
        if isinstance(typename, str):
            try:
                return self.type_dict[typename]
            except KeyError:
                from pyopencl.compyte.dtypes import NAME_TO_DTYPE
                return NAME_TO_DTYPE[typename]
        else:
            return np.dtype(typename)

    def render_arg(self, arg_placeholder):
        return arg_placeholder.target_class(
                self.parse_type(arg_placeholder.typename),
                arg_placeholder.name)

    _C_COMMENT_FINDER = re.compile(r"/\*.*?\*/")

    def render_argument_list(self, *arg_lists):
        all_args = []

        for arg_list in arg_lists:
            if isinstance(arg_list, str):
                if arg_list.startswith("//CL//"):
                    arg_list = arg_list[6:]
                arg_list = self._C_COMMENT_FINDER.sub("", arg_list)
                arg_list = arg_list.replace("\n", " ")

                all_args.extend(arg_list.split(","))
            else:
                all_args.extend(arg_list)

        from pyopencl.compyte.dtypes import parse_c_arg_backend
        parsed_args = []
        for arg in all_args:
            if isinstance(arg, str):
                arg = arg.strip()
                if not arg:
                    continue

                ph = parse_c_arg_backend(arg,
                        _ScalarArgPlaceholder, _VectorArgPlaceholder,
                        name_to_dtype=lambda x: x)
                parsed_arg = self.render_arg(ph)

            elif isinstance(arg, Argument):
                parsed_arg = arg
            elif isinstance(arg, tuple):
                parsed_arg = ScalarArg(self.parse_type(arg[0]), arg[1])

            parsed_args.append(parsed_arg)

        return parsed_args

    def get_type_decl_preamble(self, device, decl_type_names, arguments=None):
        cdl = _CDeclList(device)

        for typename in decl_type_names:
            cdl.add_dtype(self.parse_type(typename))

        if arguments is not None:
            cdl.visit_arguments(arguments)

        return cdl.get_declarations()





class KernelTemplateBase(object):
    def __init__(self, template_processor=None):
        self.template_processor = template_processor

        self.build_cache = {}
        _first_arg_dependent_caches.append(self.build_cache)

    def get_preamble(self):
        pass

    _TEMPLATE_PROCESSOR_PATTERN = re.compile(r"^//CL(?::([a-zA-Z0-9_]+))?//")

    @memoize_method
    def get_text_template(self, txt):
        proc_match = self._TEMPLATE_PROCESSOR_PATTERN.match(txt)
        tpl_processor = None

        if proc_match is not None:
            tpl_processor = proc_match.group(1)
            # chop off //CL// mark
            txt = txt[len(proc_match.group(0)):]
        if tpl_processor is None:
            tpl_processor = self.template_processor

        if tpl_processor is None or tpl_processor == "none":
            return _SimpleTextTemplate(txt)
        elif tpl_processor == "printf":
            return _PrintfTextTemplate(txt)
        elif tpl_processor == "mako":
            return _MakoTextTemplate(txt)
        else:
            raise RuntimeError("unknown template processor '%s'" % proc_match.group(1))

    def get_renderer(self, type_values, var_values, context=None, options=[]):
        return _TemplateRenderer(self, type_values, var_values)

    def build(self, context, *args, **kwargs):
        """Provide caching for an :meth:`build_inner`."""

        cache_key = (context, args, tuple(sorted(kwargs.iteritems())))
        try:
            return self.build_cache[cache_key]
        except KeyError:
            result = self.build_inner(context, *args, **kwargs)
            self.build_cache[cache_key] = result
            return result

# }}}

# vim: foldmethod=marker
