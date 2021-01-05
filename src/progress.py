def printProgressBar(iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    pbar = fill * filledLength + '-' * (length - filledLength)

    print(f'\r{prefix} |{pbar}| {percent}% {suffix}', end = printEnd)

    # Prints new line on complete
    if iteration == total:
        print()
