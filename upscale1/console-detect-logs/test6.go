package main

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strconv"
        "strings"
        "flag"
	"sync"
)

func main() {
	var commandrun string
	//	command := "upscayl-realesrgan -i /srcfolder/thumb0786.png -o /resultfolder/thumb0786.png -s 4 -m resources/models -n realesrgan-x4plus -f png -g 0"

	var number int

	// Parse the command line arguments
	flag.IntVar(&number, "num", 6, "Specify a number")
	flag.Parse()

	// Use the value of the --num argument
	fmt.Println("Number:", number)

	readFile, err := os.Open( strconv.Itoa(number) + "-tmp.txt")

	if err != nil {
		fmt.Println(err)
	}
	defer readFile.Close()
	scanner := bufio.NewScanner(readFile)
	for scanner.Scan() {
		line := scanner.Text()
		commandrun = line
		// Split the line by spaces
		parts := strings.Split(line, " ")
		if len(parts) == 0 {
			continue
		}
	}
	readFile.Close()

	parts := strings.Fields(commandrun)
	cmd := exec.Command(parts[0], parts[1:]...)

	// Create pipes to capture the command output and error
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		log.Fatal(err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		log.Fatal(err)
	}

	// Create buffered readers for the command output and error
	outReader := bufio.NewReader(stdout)
	errReader := bufio.NewReader(stderr)

	// Start the command
	if err := cmd.Start(); err != nil {
		log.Fatal(err)
	}

	// Create a wait group to synchronize goroutines
	var wg sync.WaitGroup
	wg.Add(2)

	// Read the command output line by line in a separate goroutine
	go func() {
		defer wg.Done()

		for {
			line, err := outReader.ReadString('\n')
			if err != nil {
				break
			}

			fmt.Print("1", line)
		}
	}()

	// Read the command error line by line in a separate goroutine
	go func() {
		defer wg.Done()

		for {
			line, err := errReader.ReadString('\n')
			if err != nil {
				break
			}

			fmt.Print(line)
			if strings.Contains(line, "FINISHME:") {
				//"CCS for 3D textures is disabled, but a workaround is available.") {
				fmt.Println("Works with GPU good")
			}

			if strings.Contains(line, "WARNING: lavapipe is not a conformant vulkan implementation, testing use only.") {
				// Stop the command if the line is found
                                fmt.Println("Bad. Works with CPU")

        			if err := cmd.Process.Kill(); err != nil {
					log.Fatal(err)
				}
				break
			}

		}
	}()

	// Wait for the command to finish
	if err := cmd.Wait(); err != nil {
		log.Fatal(err)
	}

	// Wait for the goroutines to finish
	wg.Wait()
}
