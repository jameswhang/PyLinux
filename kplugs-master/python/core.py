#!/usr/bin/python

import ast
from _ast import *
import struct
import ctypes
import os

WORD_SIZE = struct.calcsize("P")
VERSION   = (1, 0)

# the kplugs main class
class Plug(object):

	# kplugs commands:
	KPLUGS_REPLY = 0
	KPLUGS_LOAD = 1
	KPLUGS_EXECUTE = 2
	KPLUGS_EXECUTE_ANONYMOUS = 3
	KPLUGS_UNLOAD = 4
	KPLUGS_UNLOAD_ANONYMOUS = 5
	KPLUGS_GET_LAST_EXCEPTION = 6

	ERROR_TABLE = [
	"",
	"No more memory",
	"Recursion to deep",
	"Wrong operation",
	"Wrong variable",
	"Wrong parameter",
	"This operation is been used more the once",
	"A flow block was not terminated",
	"Some of the code was not explored",
	"Bad function name",
	"Function already exists",
	"The stack is empty",
	"Bad pointer",
	"Access outside of a buffer's limit",
	"Divide by zero",
	"Unknown function",
	"Bad number of arguments",
	"Wrong architecture",
	"Unsupported version",
	"Not a dynamic memory",
	]

	
	def __init__(self, glob = False):
		self.fd = os.open('/dev/kplugs', os.O_RDWR)
		self.funcs = []
		self.glob = glob
		self.last_exception = []

	def _exec_cmd(self, op, len1, len2, val1, val2):
		# supports only little endian version.
		header = WORD_SIZE + (1 << 7) + (VERSION[0] << 8) + (VERSION[1] << 16) + (op << 24)
		try:
			os.write(self.fd, struct.pack("PPPPP", header, len1, len2, val1, val2))
		except:
			exc = struct.unpack("P", os.read(self.fd, WORD_SIZE * 5)[WORD_SIZE * 3:WORD_SIZE * 4])[0]

			if op == Plug.KPLUGS_EXECUTE or op == Plug.KPLUGS_EXECUTE_ANONYMOUS:
				try:
					# try to get the exception parameters
					excep = ctypes.c_buffer('\0' * (WORD_SIZE * 4))
					header = WORD_SIZE + (1 << 7) + (VERSION[0] << 8) + (VERSION[1] << 16) + (Plug.KPLUGS_GET_LAST_EXCEPTION << 24)
					os.write(self.fd, struct.pack("PPPPP", Plug.KPLUGS_GET_LAST_EXCEPTION, WORD_SIZE * 4, 0, ctypes.addressof(excep), 0))
					excep = struct.unpack("PPPP", excep.raw[:WORD_SIZE * 4])
					if exc == excep[1]:
						self.last_exception = excep[2:]
				except:
					# probably we didn't fail because an exception
					pass

			if exc >= len(Plug.ERROR_TABLE):
				raise Exception("Error: 0x%x" % exc)
			raise Exception(Plug.ERROR_TABLE[exc])
		ret = os.read(self.fd, WORD_SIZE * 5)
		if len(ret) == 0:
			return
		return struct.unpack("P", ret[WORD_SIZE * 3:WORD_SIZE * 4])[0]

	def load(self, func, unhandled_return = None, function_type = 0):
		if self.glob:
			op = Plug.KPLUGS_LOAD | (1 << 7) # add the global flag
		else:
			op = Plug.KPLUGS_LOAD

		compiled = func.to_bytes(unhandled_return, function_type)
		buf = ctypes.c_buffer(compiled)

		# send the command (will throw an exception if it fails)
		func.addr = self._exec_cmd(op, len(compiled), 0, ctypes.addressof(buf), 0)

		func.plug = self
		self.funcs.append(func)

	def compile(self, code, unhandled_return = None, function_type = 0):
		# create a visitor and compile
		visitor = compiler_visitor(self)
		p = ast.parse(code)
		visitor.visit(p)

		# load all the functions
		for func in visitor.functions:
			self.load(func, unhandled_return, function_type)

		return filter(lambda i:not i.static, visitor.functions)

	def unload(self, func):
		for f in func.special_funcs.keys():
			func.special_funcs[f].unload()

		func.special_funcs = {}

		if func.anonymous:
			op = Plug.KPLUGS_UNLOAD_ANONYMOUS
			length = 0
			ptr = func.addr
		else:
			op = Plug.KPLUGS_UNLOAD
			length = len(func.name)
			name_buf = ctypes.c_buffer(func.name)
			ptr = ctypes.addressof(name_buf)

		if self.glob:
			op |= (1 << 7) # add the global flag

		# send the command (will throw an exception if it fails)
		self._exec_cmd(op, length, 0, ptr, 0)
		self.funcs.remove(func)


	def __call__(self, func, *args):
		if not func in self.funcs:
			raise Exception("This function doesn't belongs to this plug")

		if func.anonymous:
			op = Plug.KPLUGS_EXECUTE_ANONYMOUS
			length = 0
			ptr = func.addr
		else:
			op = Plug.KPLUGS_EXECUTE
			length = len(func.name)
			name_buf = ctypes.c_buffer(func.name)
			ptr = ctypes.addressof(name_buf)

		new_args = []
		bufs = []
		for arg in args:
			add = arg
			if isinstance(arg, str):
				bufs.append(ctypes.c_buffer(arg))
				add = ctypes.addressof(bufs[-1])
			new_args.append(add)
		args_buf = ctypes.c_buffer(struct.pack("P" * len(new_args), *new_args))

		# send the command (will throw an exception if it fails)
		return self._exec_cmd(op, length, len(args) * WORD_SIZE, ptr, ctypes.addressof(args_buf))

	# you MUST call this member if the plug is global or the functions will never be freed!
	def close(self):
		if self.glob:
			while len(self.funcs) != 0:
				self.unload(self.funcs[0])

		# we don't need to unload functions if it's not global because closing the file will do it for us
		self.funcs = []
		if self.fd >= 0:
			os.close(self.fd)
			self.fd = -1




RESERVED_PREFIX =	["KERNEL"]
RESERVED_NAMES = 	["VARIABLE_ARGUMENT", "ANONYMOUS", "STATIC", "ADDRESSOF", "word", "buffer", "array", "pointer", "new", "delete"]
RESERVED_FUNCTIONS = 	["_"]

# validate name
def validate_name(name):
	for pre in RESERVED_PREFIX:
		if name.startswith(pre):
			raise Exception("Illegal function name: '%s'" % (name, ))
	if name in RESERVED_NAMES or name in RESERVED_FUNCTIONS:
		raise Exception("Illegal function name: '%s'" % (name, ))

# a Function class
# you should not use it directly but through the Plug class
class Function(object):

	# Operations:
	OP_FUNCTION = 0
	OP_VARIABLE = 1
	OP_FLOW = 2
	OP_EXPRESSION = 3

	# Vars:
	VAR_UNDEF = -1
	VAR_WORD = 0
	VAR_BUF = 1
	VAR_ARRAY = 2
	VAR_POINTER = 3

	# Flow:
	FLOW_ASSIGN = 0
	FLOW_ASSIGN_OFFSET = 1
	FLOW_IF = 2
	FLOW_TRY = 3
	FLOW_WHILE = 4
	FLOW_DYN_FREE = 5

	FLOW_BLOCKEND = 6
	FLOW_THROW = 7
	FLOW_RET = 8

	# Expressions:
	EXP_WORD = 0
	EXP_VAR = 1
	EXP_STRING = 2
	EXP_EXCEPTION_VAR = 3

	EXP_ADDRESSOF = 4
	EXP_DEREF = 5

	EXP_BUF_OFFSET = 6
	EXP_ADD = 7
	EXP_SUB = 8
	EXP_MUL = 9
	EXP_DIV = 10
	EXP_AND = 11
	EXP_XOR = 12
	EXP_OR = 13
	EXP_BOOL_AND = 14
	EXP_BOOL_OR = 15
	EXP_NOT = 16
	EXP_BOOL_NOT = 17
	EXP_MOD = 18
	EXP_CALL_STRING = 19
	EXP_CALL_PTR = 20
	EXP_CALL_END = 21
	EXP_CMP_EQ = 22
	EXP_CMP_UNSIGN = 23
	EXP_CMP_SIGN = 24
	EXP_DYN_ALLOC = 25
	EXP_ARGS = 26
	EXP_EXP = 27

	FUNC_VARIABLE_ARGUMENT = 1
	FUNC_EXTERNAL = 2

	# expression operation types:

	BINOP =		{
				Add : EXP_ADD,
				Sub : EXP_SUB,
				Mult : EXP_MUL,
				Div : EXP_DIV,
				BitAnd : EXP_AND,
				BitOr : EXP_OR,
				Mod : EXP_MOD,
			}

	UNARYOP =	{
				Not: EXP_BOOL_NOT,
				Invert: EXP_NOT,
			}

	BOOLOP =	{
				Or: EXP_BOOL_OR,
				And: EXP_BOOL_AND,
			}


	# variable types:
	VARNAMES = 	{
				"word": VAR_WORD,
				"pointer": VAR_POINTER,
				"buffer": VAR_BUF,
				"array": VAR_ARRAY,
			}


	def __init__(self, name):
		validate_name(name)
		self.name = name
		self.new_var = 1
		self.all_vars = {}
		self.vars = [] # the order is importand here
		self.args = [] # the order is importand here
		self.string_table = [] # the order is importand here
		self.anonymous = False
		self.static = False
		self.special_funcs = {}

	# get a function type opcode
	def _get_func(self, 	args,
				name,
				return_exception_value = 0,
				error_return = 0,
				function_type = 0):
		return {	"op" : Function.OP_FUNCTION, 
				"min_args" : self.min_args,
				"return_exception_value" : return_exception_value,
				"name" : name,
				"error_return" : error_return,
				"function_type" : function_type }

	# get a variable type opcode
	def _get_var(self, typ, is_arg = 0, size = WORD_SIZE, init = 0, flags = 0):
		if typ == Function.VAR_UNDEF:
			typ = Function.VAR_WORD
		return {	"op" : Function.OP_VARIABLE, 
				"type" : typ,
				"is_arg" : is_arg,
				"size" : size,
				"init" : init,
				"flags" : flags }

	# get a flow type opcode
	def _get_flow(self, typ, val1 = 0, val2 = 0, val3 = 0):
		return {	"op" : Function.OP_FLOW, 
				"type" : typ,
				"val1" : val1,
				"val2" : val2,
				"val3" : val3 }

	# get an expression type opcode
	def _get_exp(self, typ, val1 = 0, val2 = 0, force = False):
		if typ == Function.EXP_VAR:
			val1 = self._get_var_id(val1)
			if val1["type"] == Function.VAR_ARRAY or val1["type"] == Function.VAR_BUF:
				return self._get_exp(Function.EXP_ADDRESSOF, val1["id"])
			val1 = val1["id"]
			if not force:
				return val1
		return {	"op" : Function.OP_EXPRESSION, 
				"type" : typ,
				"val1" : val1,
				"val2" : val2 }

	# get the id of a variable
	def _get_var_id(self, var_name, size = WORD_SIZE, create = False, typ = VAR_WORD, init = 0, flags = 0):
		if var_name not in self.all_vars:
			if not create:
				# the variable dosen't exists!
				raise Exception("Variable '%s' used before assignment" % (var_name, ))
			if var_name in RESERVED_NAMES:
				raise Exception("Illegal variable name: '%s'" % (var_name, ))
			self.vars.append(var_name)
			self.all_vars[var_name] = {"id":self.new_var, "type":typ, "size":size, "init":init, "flags":flags}
			self.new_var += 1
		ret = self.all_vars[var_name]
		if ret["type"] == Function.VAR_UNDEF:
			self.all_vars[var_name] = {"id":self.all_vars[var_name]["id"], "type":typ, "size":size, "init":init, "flags":flags}
		return self.all_vars[var_name]

	# arrange the blocks and set offsets values where it is needed
	def _order_blocks(self, block):
		this_block = []
		self.all_blocks.append(this_block)

		if type(block) == dict:
			# this is an expression bloc
			self.end += 1
			to_add = {}
			for i in block.keys():
				j = block[i]
				if type(block[i]) == list or type(block[i]) == dict:
					j = self.end # this block will be located in offset self.end
					self._order_blocks(block[i])
				to_add[i] = j
			this_block.append(to_add)

		elif type(block) == list:
			# this is a flow block
			self.end += len(block)
			for cmd in block:
				to_add = {}
				for i in cmd.keys():
					j = cmd[i]
					if type(cmd[i]) == list or type(cmd[i]) == dict:
						j = self.end # this block will be located in offset self.end
						self._order_blocks(cmd[i])
					to_add[i] = j
				this_block.append(to_add)
		else:
			# we should never get here!
			raise Exception("order_blocks unexpected behavior")

	# translate the arranged blocks to bytes
	def _translate(self):
		ret = ""
		for block in self.all_blocks:
			if block["op"] == Function.OP_FUNCTION:
				ret += struct.pack("PPPP",	block["op"] | (block["min_args"] << 2) | (block["return_exception_value"] << 7),
								block["name"],
								block["error_return"],
								block["function_type"])
			elif block["op"] == Function.OP_VARIABLE:
				ret += struct.pack("PPPP",	block["op"] | (block["type"] << 2) | (block["is_arg"] << 7),
								block["size"],
								block["init"],
								block["flags"])
			elif block["op"] == Function.OP_FLOW:
				ret += struct.pack("PPPP",	block["op"] | (block["type"] << 2),
								block["val1"],
								block["val2"],
								block["val3"])

			elif block["op"] == Function.OP_EXPRESSION:
				ret += struct.pack("PPPP",	block["op"] | (block["type"] << 2),
								block["val1"],
								block["val2"],
								0)
			else:
				# we should never get here!
				raise Exception("Unknown block type")
		return ret

	# generate the string table
	def _generate_string_table(self):
		return '\0'.join(self.string_table) + '\0'

	# return the index of a string
	def _get_string_value(self, string):
		if string[:-1].count('\0') != 0:
			raise Exception("Strings could not have nulls inside")
		if len(string) > 0 and string[-1] == '\0':
			string = string[:-1]

		if self.string_table.count(string) == 0:
			self.string_table.append(string)
		return self.string_table.index(string) + 1


	# generate bytes from a compiled function
	def to_bytes(self, unhandled_return = None, function_type = 0):
		# if unahdnled_return stays None the default return value (if an exception occured) will be the exception value

		if unhandled_return == None:
			ret_exc = 1
			ret_value = 0
		else:
			ret_exc = 0
			ret_value = unhandled_return

		name = 0

		# anonymous functions has no name
		if not self.anonymous:
			name = self._get_string_value(self.name)

		# add the function opcode and the variables opcodes
		self.all_blocks = [[self._get_func(len(self.args), name, ret_exc, ret_value, self.function_type | function_type)]]
		for i in xrange(len(self.all_vars)):
			if i < self.max_args:
				is_arg = 1
				typ = self.all_vars[self.args[i]]["type"]
				size = self.all_vars[self.args[i]]["size"]
				init = self.all_vars[self.args[i]]["init"]
				flags = self.all_vars[self.args[i]]["flags"]
			else:
				is_arg = 0
				typ = self.all_vars[self.vars[i - len(self.args)]]["type"]
				size = self.all_vars[self.vars[i - len(self.args)]]["size"]
				init = self.all_vars[self.vars[i - len(self.args)]]["init"]
				flags = self.all_vars[self.vars[i - len(self.args)]]["flags"]
			self.all_blocks.append([self._get_var(typ, is_arg = is_arg, size = size, init = init, flags = flags)])

		# arrange the blocks in the right order
		self.end = len(self.all_blocks)
		self._order_blocks(self.final)
		
		# flatten everything
		all_blocks = []
		for block in self.all_blocks:
			all_blocks += block
		self.all_blocks = all_blocks

		# return the bytes
		return self._translate() + self._generate_string_table()

	def unload(self):
		self.plug.unload(self)

	def __call__(self, *args):
		return self.plug(self, *args)


# the ast visitor class
# create the compiled function(s) class(es)
class compiler_visitor(ast.NodeVisitor):

	def __init__(self, plug):
		ast.NodeVisitor.__init__(self)
		self.in_function = False
		self.functions = []
		self.func = None
		self.block_stoped = False
		self.cur_frame = []
		self.variable_argument_funcs = []
		self.anonymous_funcs = []
		self.static_funcs = []
		self.consts = {}
		self._last_temp_var = 0
		self.plug = plug

	# add a flow opcode in the current frame
	def _create_flow(self, typ, val1 = 0, val2 = 0, val3 = 0):
		self.cur_frame[-1].append(self.func._get_flow(typ, val1, val2, val3))

	# start a new flow frame
	def _flow_new(self):
		self.cur_frame.append([])

	# return from this flow frame
	def _flow_ret(self, last = False):
		frame = self.cur_frame[-1]
		if not self.block_stoped:
			if last:
				self._create_flow(Function.FLOW_RET, self.func._get_exp(Function.EXP_WORD, 0))
			else:
				# the frame has ended without any ending block, so we should end it
				self._create_flow(Function.FLOW_BLOCKEND)

		self.block_stoped = False
		self.cur_frame = self.cur_frame[:-1]
		return frame


	# parse a call to a builtin "function" (a.k.a - the definition of a variable)
	def _parse_builtin_call(self, node, is_expr = False):
		mult = 1
		flags = 0
		values = []
		is_first = True
		for arg in node.args:
			if is_expr and is_first:
				# ignoring the first argument
				is_first = False
				continue
			if type(arg) == Num:
				values.append(arg.n)
			elif type(arg) == Name and self.consts.has_key(arg.id):
				values.append(self.consts[arg.id])
			else:
				raise Exception("Invalid assign")

		if node.func.id == "array":
			mult = WORD_SIZE
		if node.func.id == "word" or node.func.id == "pointer":
			size = WORD_SIZE
			init = 0
			if len(node.args) > 1:
				raise Exception("Invalid assign")

			if len(values) >= 1:
				init = values[0]
		else:
			if len(values) == 0 or len(values) > 2:
				raise Exception("Invalid assign")
			size = values[0]
			init = 0
			if len(values) >= 2:
				init = values[0]
		return Function.VARNAMES[node.func.id], size * mult, init, flags

	# parse one assignment (meaning - one target and one value)
	def _one_assign(self, target, value, value_explored = False):

		if type(value) == Call:
			# check if this is a variable definition assignment
			if type(value.func) == Name and value.func.id in Function.VARNAMES.keys():
				if self.func.all_vars.has_key(target.id):
					raise Exception("Variable '%s' already exists" % (target.id, ))

				typ, size, init, flags = self._parse_builtin_call(value)

				if self.consts.has_key(target.id):
					raise Exception("Assigning to a constant")

				self.func._get_var_id(target.id, size = size, create = True, typ = typ, init = init, flags = flags)
				return

		var = None

		if not value_explored:
			value = self.visit(value)
		if type(target) == Name:
			# the target is a variable
			if self.consts.has_key(target.id):
				raise Exception("Assigning to a constant")

			if self.func.all_vars.has_key(target.id):
				var = self.func.all_vars[target.id]
			if var and (var["type"] == Function.VAR_BUF or var["type"] == Function.VAR_ARRAY):
				raise Exception("Cannot assign to a buffer or an array")

			# if the variable dosen't exist, it will be defined as a word
			self._create_flow(	Function.FLOW_ASSIGN,
						self.func._get_var_id(target.id, create = True)["id"],
						value)

		elif type(target) == Subscript:
			# the target may be an offset assignment

			if type(target.value) != Name or type(target.slice) != Index:
				raise Exception("Unsupported assign type")

			if not self.func.all_vars.has_key(target.value.id):
				raise Exception("Variable '%s' used before assignment" % (target.value.id, ))

			var = self.func.all_vars[target.value.id]
			if var and (var["type"] == Function.VAR_WORD):
				raise Exception("Variable '%s' cannot be used as a pointer" % (target.value.id, ))

			# handle assignments of characters
			if (var["type"] == Function.VAR_BUF or var["type"] == Function.VAR_POINTER) and isinstance(value, dict) and value["op"] == Function.OP_EXPRESSION and value["type"] == Function.EXP_STRING:
				value = self.func._get_exp(Function.EXP_DEREF, value, 1)

			self._create_flow(	Function.FLOW_ASSIGN_OFFSET,
						var["id"],
						self.visit(target.slice.value),
						value)
		elif isinstance(target, str):
			# should happen only with a temporary variable so it can't be a constant
			self._create_flow(	Function.FLOW_ASSIGN,
						self.func._get_var_id(target, create = True)["id"],
						value)

		else:
			raise Exception("Unsupported assign type")

	# create a temporary variable - the name of the variable is not a python valid name, so there can be no conflicts
	def _get_temp_var(self):
		ret = '.tempvar%d' % (self._last_temp_var, )
		self._last_temp_var += 1
		return ret

	def _create_fstring_function(self, num_args):
		if self.func.special_funcs.has_key("fstring%d" % num_args):
			return self.func.special_funcs["fstring%d" % num_args]
		args = ', '.join(["arg%d" % (i, ) for i in xrange(num_args)])
		ret = self.plug.compile(r'''
VARIABLE_ARGUMENT("KERNEL_snprintf")

ANONYMOUS("fstring_function")
ERROR_PARAM = 5

def fstring_function(%s):
	length = KERNEL_snprintf(0, 0, %s)
	buf = new(length + 1)
	if KERNEL_snprintf(buf, length + 1, %s) != length:
		raise ERROR_PARAM
	return buf
''' % (args, args, args))[0]
		self.func.special_funcs["fstring%d" % num_args] = ret
		return ret


	# this is the callback that will be called if the script has an unknown node type
	def generic_visit(self, node):
		raise Exception("Unknown type: %s" % str(type(node)))

	def visit_Module(self, node):
		for obj in node.body:
			self.visit(obj)

	def visit_FunctionDef(self, node):
		if self.in_function:
			# you can't create a function inside a function
			raise Exception("Defining a function inside a function")

		self.func = Function(node.name)
		self.functions.append(self.func)
		self.in_function = True

		# set flags
		self.func.function_type = 0
		if node.name in self.variable_argument_funcs:
			self.func.function_type |= Function.FUNC_VARIABLE_ARGUMENT
		if node.name in self.anonymous_funcs:
			self.func.anonymous = True
		if node.name in self.static_funcs:
			self.func.static = True

		# parse arguments
		self.func.args = []
		self.func.max_args = len(node.args.args)
		self.func.min_args = self.func.max_args - len(node.args.defaults)

		defaults = [None] * self.func.min_args + node.args.defaults
		args = node.args.args
		for arg in xrange(len(args)):
			if type(args[arg]) != Name:
				raise Exception("Argument must be a Name")
			if args[arg].id in self.func.all_vars.keys():
				raise Exception("Two arguments with the same name!")

			size = WORD_SIZE
			init = 0
			flags = 0
			if defaults[arg]:
				if type(defaults[arg]) == Num:
					init = defaults[arg].n

				elif type(arg) == Name and self.consts.has_key(arg.id):
					values.append(self.consts[arg.id])
				else:
					raise Exception("Unsupported default value")
			self.func.args.append(args[arg].id)

			# add the new argument
			self.func.all_vars[args[arg].id] = {	"id":self.func.new_var,
								"type":Function.VAR_UNDEF,
								"size":size,
								"init":init,
								"flags":flags}
			self.func.new_var += 1

		# parse the flow
		body = self._flow_new()
		for obj in node.body:
			self.visit(obj)
			if self.block_stoped:
				break

		self.in_function = False

		self.func.final = self._flow_ret(True)


	def visit_Assign(self, node):
		if len(node.targets) != 1:
			raise Exception("Must be simple targets")

		target = node.targets[0]

		if not self.in_function:
			if type(target) == Name and type(node.value) == Num:
				# this is a constant assignment
				if self.consts.has_key(target.id):
					raise Exception("Redefinition of a constant")
				validate_name(target.id)

				self.consts[target.id] = node.value.n
				return
			else:
				raise Exception("All expressions must be in a function")

		if type(target) == Tuple or type(target) == List:
			if type(node.value) != Tuple and type(node.value) != List:
				raise Exception("Value is not iterable")
			if len(node.value.elts) != len(target.elts):
				raise Exception("Not the same number of targets and values")

			# copy the the values to temporary variables and then copy from the temporary variables to the targets
			# the temporary variable's name starts with a "." so they can't be use as a normal variable
			#
			# the reason to do it like this is to allow assignments like:
			#	a,b = b,a
			temp_vars = []
			for el in xrange(len(target.elts)):
				temp_vars.append(self._get_temp_var())
				self._one_assign(temp_vars[-1], node.value.elts[el])
			for el in xrange(len(target.elts)):
				self._one_assign(target.elts[el], self.func._get_exp(Function.EXP_VAR, temp_vars[el]), True)
		else:
			# one simple assignment
			self._one_assign(target, node.value)

	def visit_AugAssign(self, node):
		self._one_assign(node.target,
				self.func._get_exp(Function.BINOP[type(node.op)], self.visit(node.target), self.visit(node.value)),
				True)

	def visit_Subscript(self, node):
		# the target may be an buffer offset dereference

		if not self.in_function:
			raise Exception("All expressions must be in a function")

		if type(node.slice) != Index:
			raise Exception("Unsupported dereference type")

		if type(node.value) == Name:
			if not self.func.all_vars.has_key(node.value.id):
				raise Exception("Variable used before assignment")

			var = self.func.all_vars[node.value.id]
			if var and (var["type"] == Function.VAR_WORD):
				# we cannot use a word as a pointer
				raise Exception("Invalid dereference")
			return self.func._get_exp(Function.EXP_BUF_OFFSET, var["id"], self.visit(node.slice.value))

		else:
			return self.func._get_exp(Function.EXP_DEREF, self.func._get_exp(Function.EXP_ADD, self.visit(node.value), self.visit(node.slice.value)), 1)

	def visit_Expr(self, node):
		if not self.in_function:
			if 	type(node.value) == Call and \
				node.value.func.id == "VARIABLE_ARGUMENT" and \
				len(node.value.args) == 1 and \
				type(node.value.args[0]) == Str:

				self.variable_argument_funcs.append(node.value.args[0].s)
				return
			elif	type(node.value) == Call and \
				node.value.func.id == "ANONYMOUS" and \
				len(node.value.args) == 1 and \
				type(node.value.args[0]) == Str:
				self.anonymous_funcs.append(node.value.args[0].s)
				return
			elif	type(node.value) == Call and \
				node.value.func.id == "STATIC" and \
				len(node.value.args) == 1 and \
				type(node.value.args[0]) == Str:
				self.static_funcs.append(node.value.args[0].s)
				return
			raise Exception("All expressions must be in a function")
		else:
			if type(node.value) == Call and type(node.value.func) == Name and node.value.func.id in Function.VARNAMES:
				if len(node.value.args) == 0 or type(node.value.args[0]) != Name:
					raise Exception("Wrong syntax of argument definition")
				name = node.value.args[0].id

				typ, size, init, flags = self._parse_builtin_call(node.value, is_expr = True)

				self.func._get_var_id(name, create = True, size = size, typ = typ, init = init, flags = flags)
			else:
				self._create_flow(Function.FLOW_ASSIGN, self.func._get_var_id("_", create = True)["id"], self.visit(node.value))

	def visit_If(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		# parse the test expression
		test = self.visit(node.test)

		# parse the "if" flow
		self._flow_new()
		for obj in node.body:
			self.visit(obj)
			if self.block_stoped:
				break
		body = self._flow_ret()

		# parse the "else" flow
		self._flow_new()
		for obj in node.orelse:
			self.visit(obj)
			if self.block_stoped:
				break
		orelse = self._flow_ret()

		self._create_flow(Function.FLOW_IF, test, body, orelse)


	def visit_Pass(self, node):
		pass # :)

	def visit_TryExcept(self, node):
		# parse the "try" flow
		self._flow_new()
		for obj in node.body:
			self.visit(obj)
			if self.block_stoped:
				break
		body = self._flow_ret()

		self._flow_new()
		if len(node.handlers) != 1:
			raise Exception("Unknown try-except parameters")

		# handle the exception variable
		if not node.handlers[0].type is None or not node.handlers[0].name is None:
			if 	type(node.handlers[0].type) == Name and type(node.handlers[0].name) == Name:
				name = node.handlers[0].name.id
				typ = node.handlers[0].type.id
				if typ != 'word' and typ != 'pointer':
					raise Exception("Wrong exception type")
				if self.func.all_vars.has_key(name) and self.func.all_vars[name]["type"] != Function.VARNAMES[typ]:
					raise Exception("Trying to change a variable's type")
				typ = Function.VARNAMES[typ]

			elif	type(node.handlers[0].type) == Name and node.handlers[0].name is None:
				name = node.handlers[0].type.id
				typ = Function.VARNAMES["word"]
			else:
				raise Exception("Unsupported exception syntax")

			# create the variable if it dosen't exists
			self.func._get_var_id(name, create = True, typ = typ)

			self._one_assign(name, self.func._get_exp(Function.EXP_EXCEPTION_VAR), True)

		# parse the "except" flow
		for obj in node.handlers[0].body:
			self.visit(obj)
			if self.block_stoped:
				break
		handlers = self._flow_ret()

		self._create_flow(Function.FLOW_TRY, body, handlers)

	def visit_While(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		# parse the test expression
		test = self.visit(node.test)

		# parse the "while" flow
		self._flow_new()
		for obj in node.body:
			self.visit(obj)
			if self.block_stoped:
				break
		body = self._flow_ret()

		self._create_flow(Function.FLOW_WHILE, test, body)

	def visit_Compare(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		left = self.visit(node.left)

		if len(node.ops) != 1 or len(node.comparators) != 1:
			raise Exception("Unsupported compare structure")
		comparators = self.visit(node.comparators[0])
		if type(node.ops[0]) == Lt:
			return self.func._get_exp(Function.EXP_CMP_SIGN, left, comparators)
		if type(node.ops[0]) == LtE:
			invers = self.func._get_exp(Function.EXP_CMP_SIGN, comparators, left)
			return self.func._get_exp(Function.EXP_BOOL_NOT, invers)
		if type(node.ops[0]) == Gt:
			return self.func._get_exp(Function.EXP_CMP_SIGN, comparators, left)
		if type(node.ops[0]) == Eq:
			return self.func._get_exp(Function.EXP_CMP_EQ, left, comparators)
		if type(node.ops[0]) == NotEq:
			value = self.func._get_exp(Function.EXP_CMP_EQ, left, comparators)
			return self.func._get_exp(Function.EXP_BOOL_NOT, value)

		raise Exception("Unknown operation: %s" % (str(type(node.ops[0])), ))


	def visit_Name(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		if self.consts.has_key(node.id):
			return self.func._get_exp(Function.EXP_WORD, self.consts[node.id])
		else:
			return self.func._get_exp(Function.EXP_VAR, node.id)

	def visit_Return(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		ret = self.visit(node.value)
		self._create_flow(Function.FLOW_RET, ret)
		self.block_stoped = True

	def visit_BinOp(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		if type(node.op) == Mod and type(node.left) == Str:
			if type(node.right) == Tuple or type(node.right) == List:
				args = node.right.elts
			else:
				args = [node.right]

			new_args = []
			for arg in args:
				if type(arg) == Name:
					arg = self.func._get_exp(Function.EXP_VAR, arg.id, force = True)
				else:
					arg = self.visit(arg)

				if type(arg) == list:
					arg = self.func._get_exp(Function.EXP_EXP, arg)

				new_args .append(arg)

			ret = [self.func._get_exp(Function.EXP_CALL_PTR, self.func._get_exp(Function.EXP_WORD, self._create_fstring_function(len(args) + 1).addr))]
			ret.append(self.visit(node.left))
			ret += new_args
			ret.append(self.func._get_exp(Function.EXP_CALL_END))
			return ret
			

		left = self.visit(node.left)
		right = self.visit(node.right)

		return self.func._get_exp(Function.BINOP[type(node.op)], left, right)

	def visit_UnaryOp(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		operand = self.visit(node.operand)

		if type(node.op) == USub:
			return self.func._get_exp(Function.EXP_SUB, self.func._get_exp(Function.EXP_WORD, 0), operand)
		else:
			return self.func._get_exp(Function.UNARYOP[type(node.op)], operand)

	def visit_BoolOp(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		last_value = self.visit(node.values[0])
		for value in node.values[1:]:
			new_value = self.visit(value)
			last_value = self.func._get_exp(Function.BOOLOP[type(node.op)], new_value, last_value)

		return last_value

	def visit_Call(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		if node.starargs:
			raise Exception("Functions must be simple")

		if type(node.func) != Name or self.func.all_vars.has_key(node.func.id):
			reverse = True
			flags = Function.FUNC_EXTERNAL # note: the language do not support calling variable argument functions as expressions
			val = self.visit(node.func)
			ret = [self.func._get_exp(Function.EXP_CALL_PTR, val, flags)]
		else:
			name = node.func.id
			flags = 0
			reverse = False

			if name == "ADDRESSOF" or name == "DEREF":
				if len(node.args) != 1:
					raise Exception("Error using macro %s" % (name, ))

				if type(node.args[0]) != Name:
					if name != "DEREF":
						raise Exception("Error using macro %s" % (name, ))
					return self.func._get_exp(Function.EXP_DEREF, self.visit(node.args[0]), WORD_SIZE)

				try:
					var = self.func._get_var_id(node.args[0].id)
				except:
					raise Exception("Cannot find the address of '%s'" % (node.args[0].id, ))

				if name == "DEREF":
					if var["type"] != Function.VAR_POINTER:
						raise Exception("Can dereference only pointers")
					op = Function.EXP_DEREF
					val2 = WORD_SIZE
				else:
					op = Function.EXP_ADDRESSOF
					val2 = 0
				return self.func._get_exp(op, var["id"], val2)

			elif type(node.func) == Name and node.func.id in ["new", "delete"]:
				if node.func.id == "new":
					is_global = 0
					if len(node.args) == 2:
						if type(node.args[1]) != Num or (node.args[1].n != 0 and node.args[1].n != 1):
							raise Exception("Bad syntax of new")
						is_global = node.args[1].n
					elif len(node.args) != 1:
						raise Exception("Bad syntax of new")

					size = self.visit(node.args[0])

					return self.func._get_exp(Function.EXP_DYN_ALLOC, size, is_global)
				else:
					self._create_flow(Function.FLOW_DYN_FREE, self.visit(node.args[0]))
					return self.func._get_exp(Function.EXP_WORD, 0) # return 0

			if name.startswith("KERNEL_"):
				# this is the macro for using external functions
				reverse = True
				name = name[len("KERNEL_"):]
				flags |= Function.FUNC_EXTERNAL
				if node.func.id in self.variable_argument_funcs:
					flags |= Function.FUNC_VARIABLE_ARGUMENT
			ret = [self.func._get_exp(Function.EXP_CALL_STRING, self.func._get_string_value(name), flags)]

		# parse the arguments
		args = []
		for arg in node.args:
			if type(arg) == Name:
				if self.consts.has_key(arg.id):
					val = self.func._get_exp(Function.EXP_WORD, self.consts[arg.id])
				else:
					val = self.func._get_exp(Function.EXP_VAR, arg.id, force = True)
			else:
				val = self.visit(arg)
				if type(val) == list:
					val = self.func._get_exp(Function.EXP_EXP, val)
			args.append(val)

		if reverse:
			# external functions receive there arguments reversed
			args = args[::-1]
		ret += args
		ret.append(self.func._get_exp(Function.EXP_CALL_END))

		return ret

	def visit_Str(self, node):
		return self.func._get_exp(Function.EXP_STRING, self.func._get_string_value(node.s))
		

	def visit_Num(self, node):
		if not self.in_function:	
			raise Exception("All expressions must be in a function")

		return self.func._get_exp(Function.EXP_WORD, node.n)

	def visit_Delete(self, node):
		for target in node.targets:
			self._create_flow(Function.FLOW_DYN_FREE, self.visit(target))				

	def visit_Print(self, node):

		def _create_printk(formt, extra = None):
			args = [self.func._get_exp(Function.EXP_CALL_STRING, self.func._get_string_value("printk"), Function.FUNC_EXTERNAL | Function.FUNC_VARIABLE_ARGUMENT)]
			if extra: # reversed order (because it's an external function)
				if type(extra) == list:
					extra = self.func._get_exp(Function.EXP_EXP, extra)
				args.append(extra)
			args.append(self.func._get_exp(Function.EXP_STRING, self.func._get_string_value(formt)))
			args.append(self.func._get_exp(Function.EXP_CALL_END))
			self._create_flow(Function.FLOW_ASSIGN, self.func._get_var_id("_", create = True)["id"], args)

		for n in xrange(len(node.values)):
			if n:
				_create_printk(" ")
			formt = "%d"
			var = None
			if type(node.values[n]) == Str or (type(node.values[n]) == BinOp and type(node.values[n].op) == Mod and type(node.values[n].left) == Str):
				formt = "%s"
				if type(node.values[n]) == BinOp:
					var = self._get_temp_var()
					self._one_assign(var, node.values[n])
					extra = self.func._get_exp(Function.EXP_VAR, var, force = True)
				else:
					extra = self.visit(node.values[n])
				
			else:
				if type(node.values[n]) == Name:
					extra = self.func._get_exp(Function.EXP_VAR, node.values[n].id, force = True)
				else:
					extra = self.visit(node.values[n])
			_create_printk(formt, extra)
			if var:
				self._create_flow(Function.FLOW_DYN_FREE, self.func._get_var_id(var)["id"])

		if node.nl:
			_create_printk("\n")

	def visit_Raise(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		val = self.visit(node.type)
		self._create_flow(Function.FLOW_THROW, val)
		self.block_stoped = True

	def visit_NoneType(self, node):
		if not self.in_function:
			raise Exception("All expressions must be in a function")

		return self.func._get_exp(Function.EXP_WORD, 0)


