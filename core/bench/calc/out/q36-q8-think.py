def evaluate(expr: str) -> float:
    # Tokenization
    tokens = []
    i = 0
    n = len(expr)
    while i < n:
        if expr[i].isspace():
            i += 1
            continue
        if expr[i].isdigit() or expr[i] == '.':
            j = i
            has_dot = False
            while j < n and (expr[j].isdigit() or (expr[j] == '.' and not has_dot)):
                if expr[j] == '.':
                    has_dot = True
                j += 1
            num_str = expr[i:j]
            if not any(c.isdigit() for c in num_str):
                raise ValueError("Invalid number format")
            tokens.append(('NUMBER', float(num_str)))
            i = j
        elif expr[i] == '*':
            if i + 1 < n and expr[i+1] == '*':
                tokens.append(('POW', '**'))
                i += 2
            else:
                tokens.append(('STAR', '*'))
                i += 1
        elif expr[i] in '+-*/%()':
            tokens.append((expr[i], expr[i]))
            i += 1
        else:
            raise ValueError(f"Unknown character: {expr[i]}")
    tokens.append(('EOF', None))

    pos = 0
    length = len(tokens)

    def current():
        return tokens[pos]

    def consume(expected_type=None):
        nonlocal pos
        tok = tokens[pos]
        if expected_type and tok[0] != expected_type:
            raise ValueError(f"Expected {expected_type}, got {tok[0]}")
        pos += 1
        return tok

    def parse_expr():
        nonlocal pos
        left = parse_term()
        while current()[0] in ('+', '-'):
            op = consume()[0]
            right = parse_term()
            left = left + right if op == '+' else left - right
        return left

    def parse_term():
        nonlocal pos
        left = parse_power()
        while current()[0] in ('*', '/', '%'):
            op = consume()[0]
            right = parse_power()
            if op == '*':
                left = left * right
            elif op == '/':
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                left = left / right
            else:
                if right == 0:
                    raise ZeroDivisionError("modulo by zero")
                left = left % right
        return left

    def parse_power():
        nonlocal pos
        base = parse_unary()
        if current()[0] == 'POW':
            consume()
            exponent = parse_power()  # Right-associative
            return base ** exponent
        return base

    def parse_unary():
        nonlocal pos
        if current()[0] in ('+', '-'):
            op = consume()[0]
            operand = parse_unary()
            return -operand if op == '-' else operand
        return parse_primary()

    def parse_primary():
        nonlocal pos
        tok = current()
        if tok[0] == 'NUMBER':
            consume()
            return tok[1]
        elif tok[0] == '(':
            consume()
            val = parse_expr()
            if current()[0] != ')':
                raise ValueError("Unbalanced parentheses")
            consume()
            return val
        else:
            raise ValueError(f"Unexpected token: {tok[0]}")

    if current()[0] == 'EOF':
        raise ValueError("Empty expression")

    result = parse_expr()
    if current()[0] != 'EOF':
        raise ValueError("Unexpected token after expression")
    return result
