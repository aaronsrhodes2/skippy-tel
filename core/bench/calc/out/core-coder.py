import re

def evaluate(expr: str) -> float:
    """
    Evaluate arithmetic expressions without using eval, exec, compile or the ast module.
    Supports numbers (integers and decimals), operators (+, -, *, /, %, **), unary +/-, and parentheses.
    Raises ValueError for malformed input and ZeroDivisionError for division by zero.
    """
    if not expr or not expr.strip():
        raise ValueError("Empty expression")
    
    # Tokenize the expression
    tokens = _tokenize(expr)
    if not tokens:
        raise ValueError("Empty expression")
    
    # Parse and evaluate
    pos = [0]  # Use list to allow modification in nested functions
    result = _parse_expression(tokens, pos)
    
    if pos[0] < len(tokens):
        raise ValueError(f"Unexpected token '{tokens[pos[0]][1]}' after expression")
    
    return result

def _tokenize(expr: str):
    """Tokenize the expression into (type, value) tuples."""
    tokens = []
    i = 0
    n = len(expr)
    
    while i < n:
        # Skip whitespace
        if expr[i].isspace():
            i += 1
            continue
        
        # Handle numbers (including decimals)
        if expr[i].isdigit() or expr[i] == '.':
            start = i
            # Check for leading dot
            if expr[i] == '.':
                # Must be followed by digit
                if i + 1 >= n or not expr[i + 1].isdigit():
                    raise ValueError(f"Invalid number at position {i}")
                i += 1
                while i < n and expr[i].isdigit():
                    i += 1
                num_str = expr[start:i]
            else:
                # Regular integer or decimal
                while i < n and (expr[i].isdigit() or expr[i] == '.'):
                    # Multiple dots not allowed
                    if expr[i] == '.' and expr[i-1] == '.':
                        raise ValueError(f"Invalid number at position {i-1}")
                    i += 1
                num_str = expr[start:i]
                # Check for trailing dot or multiple dots
                if num_str.count('.') > 1 or (num_str[-1] == '.'):
                    raise ValueError(f"Invalid number '{num_str}'")
            try:
                value = float(num_str)
                # Convert to int if no decimal part
                if value.is_integer() and '.' not in num_str:
                    value = int(num_str)
                tokens.append(('NUMBER', value))
            except ValueError:
                raise ValueError(f"Invalid number '{num_str}'")
        
        # Handle operators and parentheses
        elif expr[i] in '+-*/%()':
            # Check for ** operator
            if expr[i] == '*' and i + 1 < n and expr[i + 1] == '*':
                tokens.append(('OP', '**'))
                i += 2
            else:
                op = expr[i]
                if op in '+-*/%':
                    tokens.append(('OP', op))
                else:  # parentheses
                    tokens.append(('PAREN', op))
                i += 1
        else:
            raise ValueError(f"Unknown character '{expr[i]}' at position {i}")
    
    return tokens

def _parse_expression(tokens, pos):
    """Parse expression with lowest precedence (additive operators)."""
    return _parse_additive(tokens, pos)

def _parse_additive(tokens, pos):
    """Parse additive expressions (+, -)."""
    left = _parse_multiplicative(tokens, pos)
    
    while pos[0] < len(tokens) and tokens[pos[0]][0] == 'OP' and tokens[pos[0]][1] in ('+', '-'):
        op = tokens[pos[0]][1]
        pos[0] += 1
        right = _parse_multiplicative(tokens, pos)
        if op == '+':
            left = left + right
        else:
            left = left - right
    
    return left

def _parse_multiplicative(tokens, pos):
    """Parse multiplicative expressions (*, /, %)."""
    left = _parse_unary(tokens, pos)
    
    while pos[0] < len(tokens) and tokens[pos[0]][0] == 'OP' and tokens[pos[0]][1] in ('*', '/', '%'):
        op = tokens[pos[0]][1]
        pos[0] += 1
        right = _parse_unary(tokens, pos)
        if op == '*':
            left = left * right
        elif op == '/':
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            left = left / right
        else:  # %
            if right == 0:
                raise ZeroDivisionError("Modulo by zero")
            left = left % right
    
    return left

def _parse_unary(tokens, pos):
    """Parse unary expressions (+, -)."""
    # Handle unary + and -
    if pos[0] < len(tokens) and tokens[pos[0]][0] == 'OP' and tokens[pos[0]][1] in ('+', '-'):
        op = tokens[pos[0]][1]
        pos[0] += 1
        operand = _parse_unary(tokens, pos)
        if op == '+':
            return operand
        else:
            return -operand
    
    return _parse_power(tokens, pos)

def _parse_power(tokens, pos):
    """Parse power expressions (**) - right-associative."""
    left = _parse_unary_min(tokens, pos)
    
    if pos[0] < len(tokens) and tokens[pos[0]][0] == 'OP' and tokens[pos[0]][1] == '**':
        pos[0] += 1
        # Right-associative: parse the rest recursively
        right = _parse_power(tokens, pos)
        return left ** right
    
    return left

def _parse_unary_min(tokens, pos):
    """Parse unary minus for power (binds tighter than **)."""
    if pos[0] < len(tokens) and tokens[pos[0]][0] == 'OP' and tokens[pos[0]][1] == '-':
        pos[0] += 1
        # Parse the next unary expression, then negate
        operand = _parse_unary_min(tokens, pos)
        return -operand
    return _parse_primary(tokens, pos)

def _parse_primary(tokens, pos):
    """Parse primary expressions (numbers, parentheses)."""
    if pos[0] >= len(tokens):
        raise ValueError("Unexpected end of expression")
    
    token_type, token_value = tokens[pos[0]]
    
    if token_type == 'NUMBER':
        pos[0] += 1
        return token_value
    
    if token_type == 'PAREN' and token_value == '(':
        pos[0] += 1
        result = _parse_expression(tokens, pos)
        if pos[0] >= len(tokens) or tokens[pos[0]][0] != 'PAREN' or tokens[pos[0]][1] != ')':
            raise ValueError("Unbalanced parentheses")
        pos[0] += 1
        return result
    
    if token_type == 'PAREN' and token_value == ')':
        raise ValueError("Unbalanced parentheses: unexpected ')'")
    
    raise ValueError(f"Unexpected token '{token_value}'")
