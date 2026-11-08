"""Pure arithmetic predicates over an already loaded JSON value."""
import ast
import math
import operator


def parse_json_expression(expression):
    """Validate all syntax, including branches skipped by short-circuit logic."""
    allowed = (ast.Expression,ast.Constant,ast.Name,ast.Load,ast.Subscript,ast.List,
               ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.USub,ast.UAdd,ast.Not,
               ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Add,ast.Sub,ast.Mult,ast.Div,
               ast.FloorDiv,ast.Mod,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.Call)
    if not isinstance(expression,str) or not expression.strip():
        raise ValueError('A JSON expression must be nonempty text')
    try:
        tree = ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError) as exc:
        raise ValueError('Invalid JSON expression syntax; use a command check for complex tests') from exc
    nodes = list(ast.walk(tree))
    if len(nodes)>512:
        raise ValueError('JSON expression exceeds the syntax bound; use a command check')
    intrinsic_names = set()
    for node in nodes:
        if isinstance(node,ast.Call):
            if (not isinstance(node.func,ast.Name) or node.func.id!='len'
                    or len(node.args)!=1 or node.keywords):
                raise ValueError('Only the len(JSON array/object/string) intrinsic is supported')
            intrinsic_names.add(node.func)
    for node in nodes:
        if not isinstance(node,allowed) or isinstance(node,ast.Name) and node.id!='data' and node not in intrinsic_names:
            raise ValueError('JSON expressions allow data subscripts, literals, numeric arithmetic, comparisons, booleans and the len intrinsic; no other calls or attributes')
        if isinstance(node,ast.Constant) and (type(node.value) not in (int,float,str,bool,type(None))
                or type(node.value) is float and not math.isfinite(node.value)):
            raise ValueError('Only finite JSON scalar literals are supported')
    return tree


def evaluate_json_expression(expression, data):
    """Interpret a restricted expression, without Python eval or side effects."""
    tree = parse_json_expression(expression)
    binary = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,
              ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod}
    comparisons = {ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
    def number(value):
        if type(value) not in (int,float) or type(value) is float and not math.isfinite(value):
            raise ValueError('Arithmetic and ordered comparisons require finite numbers, not booleans')
        return value
    def boolean(value):
        if type(value) is not bool:
            raise ValueError('JSON predicates must produce booleans, not merely truthy values')
        return value
    def equal(left,right):
        if type(left) in (int,float) and type(right) in (int,float):
            return number(left)==number(right)
        if type(left) is not type(right):
            return False
        if isinstance(left,list):
            return len(left)==len(right) and all(equal(a,b) for a,b in zip(left,right))
        if isinstance(left,dict):
            return left.keys()==right.keys() and all(equal(left[k],right[k]) for k in left)
        return left==right
    def visit(node):
        if isinstance(node,ast.Expression): return visit(node.body)
        if isinstance(node,ast.Name): return data
        if isinstance(node,ast.Constant):
            if type(node.value) not in (int,float,str,bool,type(None)):
                raise ValueError('Only JSON scalar literals are supported')
            return number(node.value) if type(node.value) in (int,float) else node.value
        if isinstance(node,ast.List): return [visit(v) for v in node.elts]
        if isinstance(node,ast.Call):
            value = visit(node.args[0])
            if type(value) not in (list,dict,str):
                raise ValueError('len requires a JSON array, object or string')
            return len(value)
        if isinstance(node,ast.Subscript):
            container,key = visit(node.value),visit(node.slice)
            if type(container) is dict and type(key) is str or type(container) is list and type(key) is int:
                return container[key]
            raise ValueError('Subscripts require an object string key or array integer index')
        if isinstance(node,ast.BinOp):
            return number(binary[type(node.op)](number(visit(node.left)),number(visit(node.right))))
        if isinstance(node,ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op,ast.Not): return not boolean(value)
            return number(value) if isinstance(node.op,ast.UAdd) else -number(value)
        if isinstance(node,ast.BoolOp):
            for child in node.values:
                value = boolean(visit(child))
                if isinstance(node.op,ast.And) and not value: return False
                if isinstance(node.op,ast.Or) and value: return True
            return value
        if isinstance(node,ast.Compare):
            left = visit(node.left)
            for op,right_node in zip(node.ops,node.comparators):
                right = visit(right_node)
                if isinstance(op,(ast.Eq,ast.NotEq)):
                    passed = equal(left,right)
                    if isinstance(op,ast.NotEq): passed = not passed
                else:
                    passed = comparisons[type(op)](number(left),number(right))
                if not passed: return False
                left = right
            return True
        raise ValueError('Unsupported JSON expression')
    try:
        return boolean(visit(tree))
    except (KeyError,IndexError,ArithmeticError) as exc:
        raise ValueError('JSON expression failed on the saved data: '+str(exc)) from exc
